import { ButtonHTMLAttributes, forwardRef, ReactNode } from "react";
import { cn } from "@/lib/utils";
import { cva, type VariantProps } from "class-variance-authority";
import { Loader2 } from "lucide-react";

const floatingButtonVariants = cva(
  "inline-flex items-center justify-center gap-2 font-medium transition-all duration-200 active:scale-[0.97] disabled:opacity-50 disabled:pointer-events-none",
  {
    variants: {
      variant: {
        primary:
          "bg-amber text-void shadow-orb-amber hover:brightness-110 hover:scale-[1.02]",
        secondary:
          "glass-card text-foreground hover:bg-white/5",
        ghost: "hover:bg-graphite text-foreground",
      },
      size: {
        sm: "h-10 px-4 text-sm rounded-lg",
        md: "h-12 px-6 text-base rounded-xl",
        lg: "h-14 px-8 text-lg rounded-xl",
        xl: "h-16 px-10 text-xl rounded-2xl",
      },
    },
    defaultVariants: {
      variant: "primary",
      size: "lg",
    },
  }
);

export interface FloatingButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof floatingButtonVariants> {
  loading?: boolean;
  icon?: ReactNode;
}

const FloatingButton = forwardRef<HTMLButtonElement, FloatingButtonProps>(
  ({ className, variant, size, loading, icon, children, disabled, ...props }, ref) => {
    return (
      <button
        ref={ref}
        className={cn(floatingButtonVariants({ variant, size }), className)}
        disabled={disabled || loading}
        {...props}
      >
        {loading ? (
          <Loader2 className="h-5 w-5 animate-spin" />
        ) : icon ? (
          icon
        ) : null}
        {children}
      </button>
    );
  }
);

FloatingButton.displayName = "FloatingButton";

export { FloatingButton };
