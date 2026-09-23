/** Observe bytes the app actually reads, including streams it later cancels.
 * Serialized into the probe browser; no extra reader, clone, request, or retry.
 */
export function installStreamReadObserver(apiOrigin) {
  const observations = [];
  globalThis.__murmurProbeStreams = observations;
  const originalFetch = globalThis.fetch.bind(globalThis);
  globalThis.fetch = async (...args) => {
    const response = await originalFetch(...args);
    if (!response.url) return response;
    const url = new URL(response.url);
    if (url.origin !== apiOrigin || !response.body ||
        (url.pathname !== "/chat" && !url.pathname.endsWith("/storyboard/stream"))) {
      return response;
    }
    const observation = { path: url.pathname, status: response.status, body: "", truncated: false };
    observations.push(observation);
    const decoder = new TextDecoder();
    let bytes = 0;
    const getReader = response.body.getReader.bind(response.body);
    response.body.getReader = (...options) => {
      const reader = getReader(...options);
      const read = reader.read.bind(reader);
      reader.read = async (...readOptions) => {
        const result = await read(...readOptions);
        bytes += result.value?.byteLength ?? 0;
        if (bytes > 1_048_576) observation.truncated = true;
        if (!observation.truncated) {
          observation.body += result.done
            ? decoder.decode()
            : decoder.decode(result.value, { stream: true });
        }
        return result;
      };
      return reader;
    };
    return response;
  };
}
