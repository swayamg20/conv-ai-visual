export const dynamic = "force-dynamic";

export function GET(): Response {
  const releaseSha = process.env.MURMUR_RELEASE_SHA?.trim();
  const voiceExperience =
    process.env.NEXT_PUBLIC_VOICE_RUNTIME === "disabled" ? "disabled" : "enabled";

  return Response.json(
    {
      service: "murmur-web",
      status: "ok",
      voice_experience: voiceExperience,
      ...(releaseSha ? { release_sha: releaseSha } : {}),
    },
    {
      status: 200,
      headers: {
        "Cache-Control": "no-store",
      },
    }
  );
}
