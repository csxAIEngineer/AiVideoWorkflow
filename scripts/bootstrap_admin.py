"""一次性本机引导：创建首个运营（admin）或本地用户账号。

在部署机本机运行，不对外网暴露；之后的开户、停用、重置密码全走管理后台
（admin.* RPC）。密码交互式输入，不落仓库、不进聊天记录。

用法：
    # 首个运营账号
    python scripts/bootstrap_admin.py --username ops_admin
    # 本地开发用户，并把历史无主项目划到该账号名下（仅本地便利，生产按 D3 保持无主）
    python scripts/bootstrap_admin.py --username shane --role user --claim-ownerless

DSN 自动从 .env 读取（AIVIDEO_PG_DSN）。
"""

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from end import auth, pg_store  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="本机引导账号（仅本机运行）")
    parser.add_argument("--username", required=True, help="登录名（3~32 位小写字母/数字/下划线）")
    parser.add_argument("--role", choices=("admin", "user"), default="admin", help="账号角色（默认 admin）")
    parser.add_argument("--claim-ownerless", action="store_true",
                        help="把全部无主项目划到该账号名下（本地开发便利；生产历史项目按 D3 保持无主）")
    args = parser.parse_args()
    if not pg_store.enabled():
        print("未配置 AIVIDEO_PG_DSN，无法引导账号。", file=sys.stderr)
        return 1
    password = getpass.getpass("初始密码（至少 %d 位）：" % auth.MIN_PASSWORD)
    confirm = getpass.getpass("再输一次：")
    if password != confirm:
        print("两次输入不一致。", file=sys.stderr)
        return 1
    try:
        user = auth.create_user(args.username, password, role=args.role, must_change_password=True)
    except auth.AuthError as error:
        print("创建失败：%s" % error, file=sys.stderr)
        return 1
    print("已创建账号 %s（id=%s，role=%s）。首次登录需改密。" % (user["username"], user["id"], user["role"]))
    if args.claim_ownerless:
        with pg_store.pool().connection() as conn:
            cur = conn.execute(
                "UPDATE projects SET user_id = %s WHERE user_id IS NULL AND deleted_at IS NULL",
                (user["id"],),
            )
            print("已把 %d 个无主项目划到 %s 名下。" % (cur.rowcount, user["username"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())