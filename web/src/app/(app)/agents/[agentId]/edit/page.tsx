"use client";

import { useParams, useRouter } from "next/navigation";
import { ArrowLeft } from "lucide-react";

export default function EditAgentPage() {
  const params = useParams();
  const router = useRouter();
  const agentId = params.agentId as string;

  return (
    <div className="min-h-screen bg-background flex items-center justify-center px-6">
      <div className="glass-card rounded-2xl p-8 text-center max-w-sm">
        <p className="text-lg font-semibold mb-2">Edit Agent</p>
        <p className="text-sm text-chalk-soft mb-6">
          Agent editing is coming soon. Agent ID: {agentId}
        </p>
        <button
          onClick={() => router.push("/")}
          className="inline-flex items-center gap-2 text-amber hover:underline font-medium text-sm"
        >
          <ArrowLeft className="h-4 w-4" />
          Back to Dashboard
        </button>
      </div>
    </div>
  );
}
