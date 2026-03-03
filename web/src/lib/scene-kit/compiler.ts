import type { SDLScene, RenderPlan, RenderStep, RenderCommand, Viewport } from "./types";
import { getComponent } from "./components";
import { LayoutState } from "./layout";

export function compileScene(sdl: SDLScene, viewport: Viewport): RenderPlan {
  const layout = new LayoutState(viewport);
  const steps: RenderStep[] = [];

  for (const sdlStep of sdl.steps) {
    const commands: RenderCommand[] = [];

    // Handle clear
    if (sdlStep.clear) {
      commands.push({ action: "clear" });
      layout.reset();
    }

    // Handle show (component rendering)
    if (sdlStep.show) {
      const renderer = getComponent(sdlStep.show.component);
      if (renderer) {
        const origin = layout.resolvePosition(
          sdlStep.show.position,
          sdlStep.show.id
        );
        const result = renderer(sdlStep.show.props, origin, viewport);
        commands.push(...result.commands);
        layout.registerElement(
          sdlStep.show.id ?? sdlStep.show.component,
          result.bounds,
          result.namedElements
        );
      }
    }

    // Stamp default animate_style so the renderer doesn't have to guess
    for (const cmd of commands) {
      if (!cmd.animate_style && cmd.action !== "clear" && cmd.action !== "highlight") {
        cmd.animate_style = (cmd.action === "text" || cmd.action === "latex") ? "fade" : "draw";
      }
    }

    // Handle highlight
    if (sdlStep.highlight) {
      const targets = Array.isArray(sdlStep.highlight)
        ? sdlStep.highlight
        : [sdlStep.highlight];

      for (const targetName of targets) {
        const resolvedId = layout.resolveNamedElement(targetName);
        if (resolvedId) {
          commands.push({
            action: "highlight",
            target_id: resolvedId,
            highlight_color: "#f59e0b",
          });
        }
      }
    }

    steps.push({ say: sdlStep.say, commands });
  }

  return { steps };
}
