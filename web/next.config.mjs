const requestedE2EDistDir = process.env.VOICE_E2E_NEXT_DIST_DIR;
if (
  requestedE2EDistDir &&
  (process.env.MURMUR_E2E_MODE !== "1" ||
    !/^\.next-voice-e2e\/[a-z0-9][a-z0-9-]{0,48}$/.test(requestedE2EDistDir))
) {
  throw new Error(
    "VOICE_E2E_NEXT_DIST_DIR requires guarded E2E mode and a safe per-run path"
  );
}

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  ...(requestedE2EDistDir ? { distDir: requestedE2EDistDir } : {}),
};

export default nextConfig;
