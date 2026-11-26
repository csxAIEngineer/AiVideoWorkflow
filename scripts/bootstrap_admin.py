"""一次性本机引导：只创建第一个运营（admin）账号。

在部署机本机运行，不对外网暴露；之后的开户、停用、重置密码全走管理后台
（admin.* RPC）。密码交互式输入，不落仓库、不进聊天记录。

用法：
    AIVIDEO_PG_DSN=postgres://... python scripts/bootstrap_admin.py --username ops_admin
"""

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from end import auth, pg_store  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="创建第一个运营账号（仅本机）")
    parser.add_argument("--username", required=True, help="登录名（3~32 位小写字母/数字/下划线）")
    args = parser.parse_args()
    if not pg_store.enabled():
        print("未配置 AIVIDEO_PG_DSN，无法引导运营账号。", file=sys.stderr)
        return 1
    password = getpass.getpass("初始密码（至少 %d 位）：" % auth.MIN_PASSWORD)
    confirm = getpass.getpass("再输一次：")
    if password != confirm:
        print("两次输入不一致。", file=sys.stderr)
        return 1
    try:
        user = auth.create_user(args.username, password, role="admin", must_change_password=True)
    except auth.AuthError as error:
        print("创建失败：%s" % error, file=sys.stderr)
        return 1
    print("已创建运营账号 %s（id=%s，role=admin）。首次登录需改密。" % (user["username"], user["id"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())