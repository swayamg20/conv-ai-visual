import { HTMLAttributes } from "react";
import { cn } from "@/lib/utils";

export type ConnectionStatus = "idle" | "connecting" | "connected" | "error";

interface StatusIndicatorProps extends HTMLAttributes<HTMLDivElement> {
  status: ConnectionStatus;
  label: string;
  pulse?: boolean;
  showDot?: boolean;
}

const statusConfig = {
  idle: {
    color: "bg-state-idle",
    shadowColor: "shadow-[hsl(var(--state-idle))]",
    textColor: "text-state-idle",
  },
  connecting: {
    color: "bg-lavender",
    shadowColor: "shadow-[hsl(var(--lavender))]",
    textColor: "text-lavender",
  },
  connected: {
    color: "bg-state-listening",
    shadowColor: "shadow-[hsl(var(--state-listening))]",
    textColor: "text-state-listening",
  },
  error: {
    color: "bg-destructive",
    shadowColor: "shadow-destructive",
    textColor: "text-destructive",
  },
};

export function StatusIndicator({
  status,
  label,
  pulse = false,
  showDot = true,
  className,
  ...props
}: StatusIndicatorProps) {
  const config = statusConfig[status];

  return (
    <div
      className={cn(
        "inline-flex items-center gap-2 glass-card px-4 py-2 rounded-full text-sm font-medium transition-all duration-300",
        className
      )}
      {...props}
    >
      {showDot && (
        <span className="relative flex h-2 w-2">
          {pulse && (
            <span
              className={cn(
                "absolute inline-flex h-full w-full animate-ping rounded-full opacity-75",
                config.color
              )}
            />
          )}
          <span
            className={cn(
              "relative inline-flex h-2 w-2 rounded-full shadow-[0_0_8px]",
              config.color,
              config.shadowColor
            )}
          />
        </span>
      )}
      <span className={cn("transition-colors duration-300", config.textColor)}>
        {label}
      </span>
    </div>
  );
}
