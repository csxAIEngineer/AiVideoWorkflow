let rpcSerial = 0;
const RPC_TIMEOUT_MS = 20000;

async function rpc(method, params) {
  // 挂起请求会让调用方的 busy 标志永久卡死：超时强制失败，交给调用方复位。
  const controller = typeof AbortController !== "undefined" ? new AbortController() : null;
  const timeout = controller ? setTimeout(() => controller.abort(), RPC_TIMEOUT_MS) : null;
  let body = null;
  try {
    const response = await fetch("/rpc", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: controller ? controller.signal : undefined,
      body: JSON.stringify({
        jsonrpc: "2.0",
        method: method,
        params: params || {},
        id: (rpcSerial += 1),
      }),
    });
    try {
      body = await response.json();
    } catch (error) {
      if (controller && controller.signal.aborted) throw error;
      body = null;
    }
  } catch (_error) {
    const timedOut = !!(controller && controller.signal.aborted);
    const failure = new Error(timedOut ? "连接中断：请求超时" : "连接中断");
    failure.code = 0;
    failure.data = null;
    failure.rpcKnown = false;
    failure.rpcTimeout = timedOut;
    throw failure;
  } finally {
    if (timeout) clearTimeout(timeout);
  }
  if (!body || body.error) {
    const rpcError = body && body.error;
    const message = (rpcError && rpcError.message) || "没有完成";
    const error = new Error(message);
    error.code = rpcError ? rpcError.code : 0;
    error.data = rpcError && rpcError.data != null ? rpcError.data : null;
    error.rpcKnown = !!rpcError;
    throw error;
  }
  return body.result;
}

// WS6 账户系统：强制登录开启时，未带有效会话的 projects.* 会返回 401。
// 只认服务端明确返回的 401（rpcKnown），网络中断/超时不算，避免误跳登录页。
function rpcNeedsLogin(error) {
  return !!(error && error.rpcKnown && error.code === 401);
}
