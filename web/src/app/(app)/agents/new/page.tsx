"use client";

import { useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { motion, AnimatePresence } from "framer-motion";
import { ArrowLeft, ArrowRight, Check, Loader2 } from "lucide-react";
import { MurmurLogoMark } from "@/components/murmur-doodles";
import { Input } from "@/components/ui/input";
import { createAgent } from "@/lib/api";

const TOTAL_STEPS = 7;

const SUBJECTS = [
  "Physics",
  "Mathematics",
  "Chemistry",
  "Biology",
  "Computer Science",
  "System Design",
  "Economics",
  "English",
  "Other",
];

const LEVELS = [
  "Class 7-8",
  "Class 9-10",
  "Class 11-12",
  "JEE / NEET Prep",
  "College Undergraduate",
  "Professional",
  "Self-learner",
];

const LEARNING_STYLES = [
  { id: "visual", label: "Visual", desc: "Diagrams, graphs, and illustrations" },
  { id: "examples", label: "Examples first", desc: "Show me examples, then explain the theory" },
  { id: "step-by-step", label: "Step-by-step", desc: "Break it down into small, sequential steps" },
  { id: "conceptual", label: "Conceptual", desc: "Explain the why before the how" },
  { id: "practice", label: "Practice-heavy", desc: "Lots of problems and exercises" },
];

const EMOJIS = [
  "🧠", "🔬", "📐", "🧮", "🎯", "🚀", "💡", "⚡",
  "🌟", "🎓", "📚", "🧪", "🔭", "🌊", "🔥", "🤖",
  "🦉", "🐙", "🦊", "🐸", "🦝", "🐲", "🦋", "🌿",
];

interface WizardState {
  name: string;
  subject: string;
  customSubject: string;
  level: string;
  goals: string;
  learningStyle: string;
  icon: string;
}

const slideVariants = {
  enter: (direction: number) => ({
    x: direction > 0 ? 80 : -80,
    opacity: 0,
  }),
  center: {
    x: 0,
    opacity: 1,
  },
  exit: (direction: number) => ({
    x: direction > 0 ? -80 : 80,
    opacity: 0,
  }),
};

export default function CreateAgentPage() {
  const router = useRouter();
  const [step, setStep] = useState(1);
  const [direction, setDirection] = useState(1);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const [state, setState] = useState<WizardState>({
    name: "",
    subject: "",
    customSubject: "",
    level: "",
    goals: "",
    learningStyle: "",
    icon: "🧠",
  });

  const update = useCallback(
    <K extends keyof WizardState>(key: K, value: WizardState[K]) => {
      setState((prev) => ({ ...prev, [key]: value }));
    },
    []
  );

  const canProceed = (): boolean => {
    switch (step) {
      case 1:
        return state.name.trim().length >= 2;
      case 2:
        return state.subject !== "" && (state.subject !== "Other" || state.customSubject.trim().length > 0);
      case 3:
        return state.level !== "";
      case 4:
        return state.goals.trim().length >= 5;
      case 5:
        return state.learningStyle !== "";
      case 6:
        return state.icon !== "";
      case 7:
        return true;
      default:
        return false;
    }
  };

  const goNext = () => {
    if (step < TOTAL_STEPS && canProceed()) {
      setDirection(1);
      setStep((s) => s + 1);
    }
  };

  const goBack = () => {
    if (step > 1) {
      setDirection(-1);
      setStep((s) => s - 1);
    } else {
      router.push("/");
    }
  };

  const handleSubmit = async () => {
    setSubmitting(true);
    setError("");
    try {
      const subject = state.subject === "Other" ? state.customSubject.trim() : state.subject;
      const description = `${subject} tutor for ${state.level} level. Learning style: ${state.learningStyle}.`;
      await createAgent({
        name: state.name.trim(),
        icon: state.icon,
        description,
        subject,
        level: state.level,
        goals: state.goals.trim(),
        learning_style: state.learningStyle,
      });
      router.push("/");
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Failed to create agent";
      setError(message);
    } finally {
      setSubmitting(false);
    }
  };

  const resolvedSubject = state.subject === "Other" ? state.customSubject.trim() : state.subject;

  return (
    <div className="min-h-screen bg-background flex flex-col">
      {/* Top Bar */}
      <nav className="px-6 py-4 flex items-center justify-between">
        <button
          onClick={goBack}
          className="inline-flex items-center gap-2 text-sm text-chalk-soft hover:text-foreground transition-colors"
        >
          <ArrowLeft className="h-4 w-4" />
          {step === 1 ? "Dashboard" : "Back"}
        </button>
        <MurmurLogoMark className="h-6 w-6" />
        <div className="w-16" /> {/* spacer */}
      </nav>

      {/* Progress Bar */}
      <div className="px-6 max-w-lg mx-auto w-full">
        <div className="flex gap-1.5">
          {Array.from({ length: TOTAL_STEPS }).map((_, i) => (
            <div
              key={i}
              className="h-1 flex-1 rounded-full transition-all duration-300"
              style={{
                backgroundColor:
                  i < step
                    ? "hsl(var(--amber))"
                    : "hsl(var(--chalk-faint) / 0.3)",
              }}
            />
          ))}
        </div>
        <p className="text-xs text-chalk-faint font-mono mt-2 text-right">
          {step} / {TOTAL_STEPS}
        </p>
      </div>

      {/* Step Content */}
      <div className="flex-1 flex items-center justify-center px-6">
        <div className="w-full max-w-lg">
          <AnimatePresence mode="wait" custom={direction}>
            <motion.div
              key={step}
              custom={direction}
              variants={slideVariants}
              initial="enter"
              animate="center"
              exit="exit"
              transition={{ duration: 0.25, ease: [0.16, 1, 0.3, 1] }}
            >
              {/* Step 1: Name */}
              {step === 1 && (
                <StepWrapper
                  title="What should we call your agent?"
                  subtitle="Give your AI tutor a name that feels right."
                >
                  <Input
                    placeholder="e.g. Newton, PhysicsGuru, Study Buddy"
                    value={state.name}
                    onChange={(e) => update("name", e.target.value)}
                    autoFocus
                    className="text-lg h-12"
                    onKeyDown={(e) => e.key === "Enter" && goNext()}
                  />
                </StepWrapper>
              )}

              {/* Step 2: Subject */}
              {step === 2 && (
                <StepWrapper
                  title="What subject or domain?"
                  subtitle="Pick the area your agent will specialize in."
                >
                  <div className="grid grid-cols-3 gap-3">
                    {SUBJECTS.map((s) => (
                      <button
                        key={s}
                        onClick={() => update("subject", s)}
                        className={`h-12 rounded-xl text-sm font-medium transition-all ${
                          state.subject === s
                            ? "bg-amber text-void"
                            : "glass-card glass-card-hover"
                        }`}
                      >
                        {s}
                      </button>
                    ))}
                  </div>
                  {state.subject === "Other" && (
                    <Input
                      placeholder="Type your subject..."
                      value={state.customSubject}
                      onChange={(e) => update("customSubject", e.target.value)}
                      autoFocus
                      className="mt-4"
                      onKeyDown={(e) => e.key === "Enter" && goNext()}
                    />
                  )}
                </StepWrapper>
              )}

              {/* Step 3: Level */}
              {step === 3 && (
                <StepWrapper
                  title="What's your level?"
                  subtitle="This helps your agent calibrate its explanations."
                >
                  <div className="flex flex-col gap-2">
                    {LEVELS.map((l) => (
                      <button
                        key={l}
                        onClick={() => {
                          update("level", l);
                        }}
                        className={`h-12 rounded-xl text-sm font-medium text-left px-4 transition-all ${
                          state.level === l
                            ? "bg-lavender text-void"
                            : "glass-card glass-card-hover"
                        }`}
                      >
                        {l}
                      </button>
                    ))}
                  </div>
                </StepWrapper>
              )}

              {/* Step 4: Goals */}
              {step === 4 && (
                <StepWrapper
                  title="What are your goals?"
                  subtitle="What do you want to achieve with this agent?"
                >
                  <textarea
                    placeholder="e.g. I want to master rotational mechanics for JEE Advanced, and I struggle with moment of inertia problems..."
                    value={state.goals}
                    onChange={(e) => update("goals", e.target.value)}
                    autoFocus
                    rows={5}
                    className="w-full bg-transparent border-b border-chalk-faint px-1 py-2 text-sm text-foreground placeholder:text-chalk-soft focus-visible:outline-none focus-visible:border-amber transition-colors resize-none"
                  />
                </StepWrapper>
              )}

              {/* Step 5: Learning Style */}
              {step === 5 && (
                <StepWrapper
                  title="How do you learn best?"
                  subtitle="Your agent will adapt its teaching style."
                >
                  <div className="flex flex-col gap-3">
                    {LEARNING_STYLES.map((ls) => (
                      <button
                        key={ls.id}
                        onClick={() => update("learningStyle", ls.id)}
                        className={`rounded-xl text-left px-5 py-4 transition-all ${
                          state.learningStyle === ls.id
                            ? "bg-sage/20 border border-sage/40"
                            : "glass-card glass-card-hover"
                        }`}
                      >
                        <p className="font-medium text-sm">{ls.label}</p>
                        <p className="text-xs text-chalk-soft mt-0.5">{ls.desc}</p>
                      </button>
                    ))}
                  </div>
                </StepWrapper>
              )}

              {/* Step 6: Icon */}
              {step === 6 && (
                <StepWrapper
                  title="Pick an icon for your agent"
                  subtitle="This will appear on your dashboard."
                >
                  <div className="grid grid-cols-8 gap-3">
                    {EMOJIS.map((e) => (
                      <button
                        key={e}
                        onClick={() => update("icon", e)}
                        className={`h-12 w-12 rounded-xl text-2xl flex items-center justify-center transition-all ${
                          state.icon === e
                            ? "bg-amber/20 border-2 border-amber scale-110"
                            : "glass-card glass-card-hover"
                        }`}
                      >
                        {e}
                      </button>
                    ))}
                  </div>
                </StepWrapper>
              )}

              {/* Step 7: Preview */}
              {step === 7 && (
                <StepWrapper
                  title="Looking good! Ready to create?"
                  subtitle="Review your agent before we bring it to life."
                >
                  <div className="glass-card rounded-2xl p-6 space-y-4">
                    <div className="flex items-center gap-4">
                      <span className="text-5xl">{state.icon}</span>
                      <div>
                        <h3 className="text-xl font-bold">{state.name}</h3>
                        <p className="text-xs text-chalk-soft font-mono">
                          {resolvedSubject} / {state.level}
                        </p>
                      </div>
                    </div>
                    <div className="border-t border-chalk-faint/20 pt-4 space-y-3">
                      <PreviewRow label="Goals" value={state.goals} />
                      <PreviewRow
                        label="Learning Style"
                        value={
                          LEARNING_STYLES.find((l) => l.id === state.learningStyle)?.label ||
                          state.learningStyle
                        }
                      />
                    </div>
                  </div>
                  {error && (
                    <p className="text-sm text-ember mt-4">{error}</p>
                  )}
                </StepWrapper>
              )}
            </motion.div>
          </AnimatePresence>
        </div>
      </div>

      {/* Bottom Navigation */}
      <div className="px-6 py-8 max-w-lg mx-auto w-full flex items-center justify-between">
        <button
          onClick={goBack}
          className="text-sm text-chalk-soft hover:text-foreground transition-colors"
        >
          {step === 1 ? "Cancel" : "Back"}
        </button>

        {step < TOTAL_STEPS ? (
          <button
            onClick={goNext}
            disabled={!canProceed()}
            className="inline-flex items-center gap-2 bg-amber text-void font-semibold px-6 py-3 rounded-xl text-sm hover:brightness-110 transition-all active:scale-[0.97] disabled:opacity-30 disabled:pointer-events-none"
          >
            Continue
            <ArrowRight className="h-4 w-4" />
          </button>
        ) : (
          <button
            onClick={handleSubmit}
            disabled={submitting}
            className="inline-flex items-center gap-2 bg-sage text-void font-semibold px-6 py-3 rounded-xl text-sm hover:brightness-110 transition-all active:scale-[0.97] disabled:opacity-50 disabled:pointer-events-none"
          >
            {submitting ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Check className="h-4 w-4" />
            )}
            Create Agent
          </button>
        )}
      </div>
    </div>
  );
}

function StepWrapper({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <h2 className="text-2xl font-bold tracking-tight mb-1">{title}</h2>
      <p className="text-sm text-chalk-soft mb-8">{subtitle}</p>
      {children}
    </div>
  );
}

function PreviewRow({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-[10px] font-mono text-chalk-faint uppercase tracking-wider mb-0.5">
        {label}
      </p>
      <p className="text-sm text-foreground">{value}</p>
    </div>
  );
}
