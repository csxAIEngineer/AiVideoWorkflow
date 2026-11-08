let rpcSerial = 0;

async function rpc(method, params) {
  const response = await fetch("/rpc", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      jsonrpc: "2.0",
      method: method,
      params: params || {},
      id: (rpcSerial += 1),
    }),
  });
  let body = null;
  try {
    body = await response.json();
  } catch (_error) {
    body = null;
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
