import { HTMLAttributes, forwardRef } from "react";
import { cn } from "@/lib/utils";
import { cva, type VariantProps } from "class-variance-authority";

const glassmorphicCardVariants = cva(
  "glass-card rounded-lg",
  {
    variants: {
      variant: {
        default: "",
        elevated: "bg-background-elevated/50",
        interactive: "glass-card-hover cursor-pointer",
      },
      shadow: {
        sm: "shadow-glass",
        md: "shadow-glass",
        lg: "shadow-glass-lg",
      },
      padding: {
        none: "p-0",
        sm: "p-3",
        md: "p-4",
        lg: "p-6",
        xl: "p-8",
      },
    },
    defaultVariants: {
      variant: "default",
      shadow: "md",
      padding: "md",
    },
  }
);

export interface GlassmorphicCardProps
  extends HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof glassmorphicCardVariants> {
  hoverable?: boolean;
}

const GlassmorphicCard = forwardRef<HTMLDivElement, GlassmorphicCardProps>(
  ({ className, variant, shadow, padding, hoverable, ...props }, ref) => {
    return (
      <div
        ref={ref}
        className={cn(
          glassmorphicCardVariants({ variant, shadow, padding }),
          hoverable && "glass-card-hover",
          className
        )}
        {...props}
      />
    );
  }
);

GlassmorphicCard.displayName = "GlassmorphicCard";

export { GlassmorphicCard };
