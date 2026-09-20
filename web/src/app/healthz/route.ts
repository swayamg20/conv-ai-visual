export const dynamic = "force-dynamic";

export function GET(): Response {
  const releaseSha = process.env.MURMUR_RELEASE_SHA?.trim();

  return Response.json(
    {
      service: "murmur-web",
      status: "ok",
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
