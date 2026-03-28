"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { useParams, useRouter } from "next/navigation";
import { motion, AnimatePresence } from "framer-motion";
import {
  ArrowLeft,
  Upload,
  Link2,
  Trash2,
  FileText,
  Globe,
  Loader2,
  CheckCircle2,
  Clock,
  AlertCircle,
  Plus,
} from "lucide-react";
import { fetchAgent, fetchResources, uploadResource, addResourceUrl, deleteResource } from "@/lib/api";
import type { Agent, Resource } from "@/lib/types";
import { MurmurLogoMark } from "@/components/murmur-doodles";

function formatBytes(bytes: number | null): string {
  if (bytes === null || bytes === 0) return "--";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
}

function formatDate(dateStr: string): string {
  const date = new Date(dateStr);
  return date.toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" });
}

function StatusBadge({ status }: { status: string }) {
  const config: Record<string, { icon: React.ReactNode; color: string; label: string }> = {
    ready: {
      icon: <CheckCircle2 className="h-3 w-3" />,
      color: "hsl(var(--sage))",
      label: "Ready",
    },
    processing: {
      icon: <Clock className="h-3 w-3 animate-spin" />,
      color: "hsl(var(--amber))",
      label: "Processing",
    },
    error: {
      icon: <AlertCircle className="h-3 w-3" />,
      color: "hsl(var(--ember))",
      label: "Error",
    },
  };

  const c = config[status] || config.processing;

  return (
    <div
      className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-mono uppercase tracking-wider"
      style={{
        backgroundColor: `${c.color}15`,
        color: c.color,
        border: `1px solid ${c.color}30`,
      }}
    >
      {c.icon}
      {c.label}
    </div>
  );
}

function ResourceTypeBadge({ type }: { type: string }) {
  const isPDF = type.toLowerCase().includes("pdf");
  return (
    <div className="w-10 h-10 rounded-xl flex items-center justify-center bg-chalk-faint/10">
      {isPDF ? (
        <FileText className="h-5 w-5 text-amber" />
      ) : (
        <Globe className="h-5 w-5 text-lavender" />
      )}
    </div>
  );
}

function SkeletonResource() {
  return (
    <div className="glass-card rounded-xl p-4 animate-pulse">
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 rounded-xl bg-chalk-faint/15" />
        <div className="flex-1 min-w-0">
          <div className="h-4 w-48 rounded bg-chalk-faint/15 mb-2" />
          <div className="h-3 w-24 rounded bg-chalk-faint/10" />
        </div>
        <div className="h-6 w-16 rounded-full bg-chalk-faint/10" />
      </div>
    </div>
  );
}

function EmptyResources() {
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      className="text-center py-16"
    >
      <svg
        viewBox="0 0 120 100"
        fill="none"
        className="w-28 h-auto mx-auto mb-6 opacity-20"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        {/* Stack of papers */}
        <rect x="20" y="15" width="60" height="75" rx="4" stroke="hsl(var(--chalk-faint))" strokeWidth="1.5" strokeDasharray="4 3" />
        <rect x="26" y="10" width="60" height="75" rx="4" stroke="hsl(var(--chalk-faint))" strokeWidth="1.5" strokeDasharray="4 3" />
        <rect x="32" y="5" width="60" height="75" rx="4" stroke="hsl(var(--chalk-faint))" strokeWidth="1.5" strokeDasharray="4 3" />
        {/* Lines on top paper */}
        <line x1="42" y1="25" x2="82" y2="25" stroke="hsl(var(--chalk-faint))" strokeWidth="1" strokeDasharray="3 3" />
        <line x1="42" y1="35" x2="78" y2="35" stroke="hsl(var(--chalk-faint))" strokeWidth="1" strokeDasharray="3 3" />
        <line x1="42" y1="45" x2="72" y2="45" stroke="hsl(var(--chalk-faint))" strokeWidth="1" strokeDasharray="3 3" />
        {/* Plus icon */}
        <line x1="60" y1="60" x2="60" y2="72" stroke="hsl(var(--amber))" strokeWidth="1.5" opacity="0.4" />
        <line x1="54" y1="66" x2="66" y2="66" stroke="hsl(var(--amber))" strokeWidth="1.5" opacity="0.4" />
      </svg>
      <h3 className="text-lg font-semibold text-foreground mb-2 font-handwriting">
        No resources yet
      </h3>
      <p className="text-sm text-chalk-soft max-w-xs mx-auto leading-relaxed">
        Upload PDFs or add URLs to give your agent reference material for smarter tutoring.
      </p>
    </motion.div>
  );
}

export default function ResourcesPage() {
  const params = useParams();
  const router = useRouter();
  const agentId = params.agentId as string;

  const [agent, setAgent] = useState<Agent | null>(null);
  const [resources, setResources] = useState<Resource[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Upload state
  const [isDragging, setIsDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // URL state
  const [urlInput, setUrlInput] = useState("");
  const [addingUrl, setAddingUrl] = useState(false);
  const [urlError, setUrlError] = useState<string | null>(null);

  // Delete state
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    try {
      setError(null);
      setLoading(true);
      const [agentData, resourcesData] = await Promise.all([
        fetchAgent(agentId),
        fetchResources(agentId),
      ]);
      setAgent(agentData);
      setResources(resourcesData);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Failed to load data";
      setError(message);
    } finally {
      setLoading(false);
    }
  }, [agentId]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const handleFileUpload = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    setUploadError(null);
    setUploading(true);

    try {
      const results: Resource[] = [];
      for (const file of Array.from(files)) {
        const resource = await uploadResource(agentId, file);
        results.push(resource);
      }
      setResources((prev) => [...results, ...prev]);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Upload failed";
      setUploadError(message);
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const handleAddUrl = async () => {
    const trimmed = urlInput.trim();
    if (!trimmed) return;
    setUrlError(null);
    setAddingUrl(true);

    try {
      const resource = await addResourceUrl(agentId, trimmed);
      setResources((prev) => [resource, ...prev]);
      setUrlInput("");
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Failed to add URL";
      setUrlError(message);
    } finally {
      setAddingUrl(false);
    }
  };

  const handleDelete = async (resourceId: string) => {
    setDeletingId(resourceId);
    try {
      await deleteResource(agentId, resourceId);
      setResources((prev) => prev.filter((r) => r.id !== resourceId));
    } catch {
      // Silently fail — the resource stays in the list
    } finally {
      setDeletingId(null);
    }
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    handleFileUpload(e.dataTransfer.files);
  };

  return (
    <div className="min-h-screen bg-background">
      {/* Header */}
      <nav className="px-6 py-4 flex items-center justify-between">
        <button
          onClick={() => router.push("/dashboard")}
          className="inline-flex items-center gap-2 text-sm text-chalk-soft hover:text-foreground transition-colors"
        >
          <ArrowLeft className="h-4 w-4" />
          Dashboard
        </button>
        <MurmurLogoMark className="h-6 w-6" />
        <div className="w-16" />
      </nav>

      <main className="max-w-2xl mx-auto px-6 pb-16">
        {/* Page Title */}
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          className="mb-8"
        >
          <div className="flex items-center gap-3 mb-1">
            {agent && <span className="text-2xl">{agent.icon || "🤖"}</span>}
            <h1 className="text-2xl font-bold tracking-tight">Resources</h1>
          </div>
          <p className="text-sm text-chalk-soft">
            {agent ? `Reference material for ${agent.name}` : "Loading..."}
          </p>
        </motion.div>

        {/* Upload Section */}
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.1 }}
          className="mb-8 space-y-4"
        >
          {/* File Drop Zone */}
          <div
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
            onClick={() => fileInputRef.current?.click()}
            className={`relative glass-card rounded-2xl p-8 text-center cursor-pointer transition-all duration-200 border-2 border-dashed ${
              isDragging
                ? "border-amber bg-amber/5"
                : "border-chalk-faint/30 hover:border-chalk-faint/50 hover:bg-chalk-faint/5"
            }`}
          >
            <input
              ref={fileInputRef}
              type="file"
              accept=".pdf"
              multiple
              className="hidden"
              onChange={(e) => handleFileUpload(e.target.files)}
            />

            {uploading ? (
              <div className="flex flex-col items-center gap-3">
                <Loader2 className="h-8 w-8 animate-spin text-amber" />
                <p className="text-sm text-chalk-soft">Uploading...</p>
              </div>
            ) : (
              <div className="flex flex-col items-center gap-3">
                <div className="w-12 h-12 rounded-xl bg-amber/10 flex items-center justify-center">
                  <Upload className="h-6 w-6 text-amber" />
                </div>
                <div>
                  <p className="text-sm font-medium text-foreground">
                    Drop PDFs here or click to browse
                  </p>
                  <p className="text-xs text-chalk-soft mt-1">
                    PDF files up to 50MB
                  </p>
                </div>
              </div>
            )}
          </div>

          {uploadError && (
            <p className="text-sm text-ember px-1">{uploadError}</p>
          )}

          {/* URL Input */}
          <div className="flex gap-2">
            <div className="flex-1 relative">
              <Link2 className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-chalk-soft" />
              <input
                type="url"
                placeholder="https://example.com/article"
                value={urlInput}
                onChange={(e) => setUrlInput(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleAddUrl()}
                className="w-full h-11 pl-10 pr-4 bg-transparent border border-chalk-faint/30 rounded-xl text-sm text-foreground placeholder:text-chalk-soft focus:outline-none focus:border-amber transition-colors"
              />
            </div>
            <button
              onClick={handleAddUrl}
              disabled={addingUrl || !urlInput.trim()}
              className="h-11 px-5 bg-lavender/15 text-lavender font-medium text-sm rounded-xl hover:bg-lavender/25 transition-all disabled:opacity-30 disabled:pointer-events-none inline-flex items-center gap-2"
            >
              {addingUrl ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Plus className="h-4 w-4" />
              )}
              Add URL
            </button>
          </div>

          {urlError && (
            <p className="text-sm text-ember px-1">{urlError}</p>
          )}
        </motion.div>

        {/* Resources List */}
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.2 }}
        >
          <h2 className="text-sm font-mono text-chalk-faint uppercase tracking-wider mb-4 px-1">
            Resources ({resources.length})
          </h2>

          {loading && (
            <div className="space-y-3">
              <SkeletonResource />
              <SkeletonResource />
              <SkeletonResource />
            </div>
          )}

          {error && !loading && (
            <div className="text-center py-8">
              <p className="text-ember text-sm mb-3">{error}</p>
              <button
                onClick={loadData}
                className="text-amber hover:underline font-medium text-sm"
              >
                Try again
              </button>
            </div>
          )}

          {!loading && !error && resources.length === 0 && <EmptyResources />}

          {!loading && !error && resources.length > 0 && (
            <div className="space-y-3">
              <AnimatePresence>
                {resources.map((resource, i) => (
                  <motion.div
                    key={resource.id}
                    layout
                    initial={{ opacity: 0, y: 8 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, x: -20 }}
                    transition={{ delay: i * 0.03 }}
                    className="glass-card glass-card-hover rounded-xl p-4"
                  >
                    <div className="flex items-center gap-3">
                      <ResourceTypeBadge type={resource.resource_type} />
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium truncate text-foreground">
                          {resource.name}
                        </p>
                        <div className="flex items-center gap-3 text-xs text-chalk-soft mt-0.5">
                          <span>{formatBytes(resource.size_bytes)}</span>
                          <span>{resource.chunk_count} chunks</span>
                          <span>{formatDate(resource.created_at)}</span>
                        </div>
                      </div>
                      <StatusBadge status={resource.status} />
                      <button
                        onClick={() => handleDelete(resource.id)}
                        disabled={deletingId === resource.id}
                        className="p-2 rounded-lg hover:bg-ember/10 transition-colors disabled:opacity-50"
                        aria-label="Delete resource"
                      >
                        {deletingId === resource.id ? (
                          <Loader2 className="h-4 w-4 animate-spin text-ember" />
                        ) : (
                          <Trash2 className="h-4 w-4 text-ember/60" />
                        )}
                      </button>
                    </div>
                  </motion.div>
                ))}
              </AnimatePresence>
            </div>
          )}
        </motion.div>
      </main>
    </div>
  );
}
