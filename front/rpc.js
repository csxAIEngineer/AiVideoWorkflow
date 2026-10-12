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
    const message = (body && body.error && body.error.message) || "没有完成";
    const error = new Error(message);
    error.code = body && body.error ? body.error.code : 0;
    throw error;
  }
  return body.result;
}
