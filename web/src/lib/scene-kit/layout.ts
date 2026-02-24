import type { Viewport, BoundingBox, RelativePosition } from "./types";

const MARGIN = 40;
const SPACING = 30;

export class LayoutState {
  private viewport: Viewport;
  private elements = new Map<string, BoundingBox>();
  private namedSubElements = new Map<string, string>();
  private cursorY: number;

  constructor(viewport: Viewport) {
    this.viewport = viewport;
    this.cursorY = MARGIN;
  }

  reset(): void {
    this.elements.clear();
    this.namedSubElements.clear();
    this.cursorY = MARGIN;
  }

  resolvePosition(
    position: RelativePosition | undefined,
    _elementId?: string
  ): { x: number; y: number } {
    if (!position) {
      return this.nextAvailable();
    }

    if (position === "center") {
      return this.centerAvailable();
    }

    if (position === "top") {
      return { x: this.viewport.width / 2 - 100, y: MARGIN };
    }

    if (position === "bottom") {
      return { x: this.viewport.width / 2 - 100, y: this.viewport.height - MARGIN - 100 };
    }

    if (position === "left") {
      return { x: MARGIN, y: this.cursorY };
    }

    if (position === "right") {
      return { x: this.viewport.width - MARGIN - 200, y: this.cursorY };
    }

    if (typeof position === "object" && "below" in position) {
      const ref = this.elements.get(position.below);
      if (ref) {
        const gap = position.gap ?? SPACING;
        return { x: ref.x, y: ref.y + ref.height + gap };
      }
    }

    if (typeof position === "object" && "rightOf" in position) {
      const ref = this.elements.get(position.rightOf);
      if (ref) {
        const gap = position.gap ?? SPACING;
        return { x: ref.x + ref.width + gap, y: ref.y };
      }
    }

    return this.nextAvailable();
  }

  registerElement(
    id: string,
    bounds: BoundingBox,
    namedElements?: Record<string, string>
  ): void {
    this.elements.set(id, bounds);
    if (namedElements) {
      for (const [name, targetId] of Object.entries(namedElements)) {
        this.namedSubElements.set(name, targetId);
      }
    }
    this.cursorY = bounds.y + bounds.height + SPACING;
  }

  resolveNamedElement(name: string): string | null {
    return (
      this.namedSubElements.get(name) ??
      (this.elements.has(name) ? name : null)
    );
  }

  private centerAvailable(): { x: number; y: number } {
    if (this.elements.size === 0) {
      return {
        x: this.viewport.width / 2 - 100,
        y: this.viewport.height / 2 - 80,
      };
    }
    return this.nextAvailable();
  }

  private nextAvailable(): { x: number; y: number } {
    const x = this.viewport.width / 2 - 100;
    const y = this.cursorY;

    // If y would overflow, shift to the right half
    if (y + 120 > this.viewport.height - MARGIN) {
      return { x: this.viewport.width / 2 + 80, y: MARGIN };
    }

    return { x, y };
  }
}
