"use client";

import * as React from "react";
import { cn } from "@/lib/utils";

interface SwitchProps extends React.InputHTMLAttributes<HTMLInputElement> {
  onCheckedChange?: (checked: boolean) => void;
}

const Switch = React.forwardRef<HTMLInputElement, SwitchProps>(
  ({ className, checked, onCheckedChange, ...props }, ref) => {
    return (
      <label className={cn("relative inline-flex h-[22px] w-10 cursor-pointer", className)}>
        <input
          type="checkbox"
          className="peer sr-only"
          ref={ref}
          checked={checked}
          onChange={(e) => onCheckedChange?.(e.target.checked)}
          {...props}
        />
        <span className="absolute inset-0 rounded-full border border-chalk-faint bg-graphite transition-all peer-checked:border-amber peer-checked:bg-amber" />
        <span className="absolute bottom-[2px] left-[2px] h-4 w-4 rounded-full bg-chalk-soft transition-all peer-checked:translate-x-[18px] peer-checked:bg-void" />
      </label>
    );
  }
);
Switch.displayName = "Switch";

export { Switch };

