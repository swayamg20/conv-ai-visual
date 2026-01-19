"use client";

import { useState, useEffect, useImperativeHandle, forwardRef, useCallback, useRef } from "react";
import dynamic from "next/dynamic";
import type { CanvasOperation } from "@/hooks/use-webrtc";
import type { ExcalidrawElement, ExcalidrawImperativeAPI } from "@/types/excalidraw";
import { convertToExcalidrawElements } from "@excalidraw/excalidraw";

// Dynamically import Excalidraw with SSR disabled (Next.js requirement)
const Excalidraw = dynamic(
  async () => (await import("@excalidraw/excalidraw")).Excalidraw,
  { ssr: false }
);

export interface ExcalidrawCanvasHandle {
  render: (operations: CanvasOperation[]) => void;
  clear: () => void;
  getState: () => string; // Export canvas state as JSON for LLM context
}

interface ExcalidrawCanvasProps {
  width?: number;
  height?: number;
  className?: string;
}

export const ExcalidrawCanvas = forwardRef<ExcalidrawCanvasHandle, ExcalidrawCanvasProps>(
  ({ width = 800, height = 600, className }, ref) => {
    const [excalidrawAPI, setExcalidrawAPI] = useState<ExcalidrawImperativeAPI | null>(null);
    const elementsMapRef = useRef<Map<string, ExcalidrawElement>>(new Map());

    // Sync user edits back to our internal state
    const handleChange = useCallback((elements: readonly any[]) => {
      // Update our internal map when user makes changes
      elementsMapRef.current.clear();
      elements.forEach(elem => {
        if (elem.id) {
          elementsMapRef.current.set(elem.id, elem as any);
        }
      });
    }, []);

    /**
     * Convert canvas operations to Excalidraw elements
     */
    const operationsToExcalidrawElements = useCallback((operations: CanvasOperation[]): ExcalidrawElement[] => {
      const skeletonElements: any[] = [];

      for (const op of operations) {
        // Handle control actions
        if (op.action === "clear") {
          elementsMapRef.current.clear();
          return [];
        }

        if (op.action === "delete") {
          const targetId = op.id || op.target_id;
          if (targetId) {
            elementsMapRef.current.delete(targetId);
          }
          continue;
        }

        // Map canvas operations to Excalidraw element skeletons
        let skeleton: any = null;

        switch (op.action) {
          case "rect":
            skeleton = {
              type: "rectangle",
              x: op.x ?? 0,
              y: op.y ?? 0,
              width: op.width ?? 100,
              height: op.height ?? 100,
              strokeColor: op.color ?? "#000000",
              backgroundColor: op.fill ?? "transparent",
              strokeWidth: op.stroke_width ?? 2,
              roughness: 1,
              roundness: null,
            };
            break;

          case "circle":
            skeleton = {
              type: "ellipse",
              x: op.x ?? 0,
              y: op.y ?? 0,
              width: op.width ?? 100,
              height: op.width ?? 100, // Use width for both to make it a circle
              strokeColor: op.color ?? "#000000",
              backgroundColor: op.fill ?? "transparent",
              strokeWidth: op.stroke_width ?? 2,
              roughness: 1,
            };
            break;

          case "ellipse":
            skeleton = {
              type: "ellipse",
              x: op.x ?? 0,
              y: op.y ?? 0,
              width: op.width ?? 100,
              height: op.height ?? 80,
              strokeColor: op.color ?? "#000000",
              backgroundColor: op.fill ?? "transparent",
              strokeWidth: op.stroke_width ?? 2,
              roughness: 1,
            };
            break;

          case "text":
            skeleton = {
              type: "text",
              x: op.x ?? 0,
              y: op.y ?? 0,
              text: op.text ?? "",
              fontSize: op.font_size ?? 16,
              fontFamily: 1, // Excalidraw uses numeric font families
              textAlign: "left",
              verticalAlign: "top",
              strokeColor: op.color ?? "#000000",
            };
            break;

          case "arrow":
            if (op.points && op.points.length >= 2) {
              const start = op.points[0];
              const end = op.points[op.points.length - 1];
              skeleton = {
                type: "arrow",
                x: start[0],
                y: start[1],
                width: end[0] - start[0],
                height: end[1] - start[1],
                strokeColor: op.color ?? "#000000",
                strokeWidth: op.stroke_width ?? 2,
                roughness: 1,
              };
            }
            break;

          case "line":
            if (op.points && op.points.length >= 2) {
              const start = op.points[0];
              const end = op.points[op.points.length - 1];
              skeleton = {
                type: "line",
                x: start[0],
                y: start[1],
                width: end[0] - start[0],
                height: end[1] - start[1],
                strokeColor: op.color ?? "#000000",
                strokeWidth: op.stroke_width ?? 2,
                roughness: 1,
              };
            }
            break;

          case "path":
            // Convert path to line for now (Excalidraw doesn't have a direct path type)
            if (op.points && op.points.length >= 2) {
              const start = op.points[0];
              const end = op.points[op.points.length - 1];
              skeleton = {
                type: "line",
                x: start[0],
                y: start[1],
                width: end[0] - start[0],
                height: end[1] - start[1],
                strokeColor: op.color ?? "#000000",
                strokeWidth: op.stroke_width ?? 2,
                roughness: 2, // More rough for freehand look
              };
            }
            break;

          case "update":
            // Update existing element
            const updateTargetId = op.id || op.target_id;
            if (updateTargetId && elementsMapRef.current.has(updateTargetId)) {
              const existingElem = elementsMapRef.current.get(updateTargetId)!;
              // Merge updates with existing element
              skeleton = {
                ...existingElem,
                // Only update provided fields
                ...(op.x !== undefined && { x: op.x }),
                ...(op.y !== undefined && { y: op.y }),
                ...(op.width !== undefined && { width: op.width }),
                ...(op.height !== undefined && { height: op.height }),
                ...(op.text !== undefined && { text: op.text }),
                ...(op.color !== undefined && { strokeColor: op.color }),
                ...(op.fill !== undefined && { backgroundColor: op.fill }),
                ...(op.stroke_width !== undefined && { strokeWidth: op.stroke_width }),
              };
              skeleton.id = updateTargetId; // Preserve ID
            }
            break;

          case "highlight":
            // Highlight existing element by updating its stroke
            const targetId = op.id || op.target_id;
            if (targetId && elementsMapRef.current.has(targetId)) {
              const elem = elementsMapRef.current.get(targetId)!;
              skeleton = {
                ...elem,
                strokeColor: "#fbbf24",
                strokeWidth: (elem.strokeWidth ?? 2) + 2,
              };
            }
            break;
        }

        if (skeleton) {
          // Preserve ID if provided
          if (op.id) {
            skeleton.id = op.id;
          }
          skeletonElements.push(skeleton);
        }
      }

      // Convert skeletons to Excalidraw elements
      if (skeletonElements.length === 0) {
        return Array.from(elementsMapRef.current.values());
      }

      const newElements = convertToExcalidrawElements(skeletonElements, { regenerateIds: false });

      // Update the elements map
      newElements.forEach(elem => {
        if (elem.id) {
          elementsMapRef.current.set(elem.id, elem as any);
        }
      });

      return Array.from(elementsMapRef.current.values());
    }, []);

    /**
     * Get current canvas state as JSON for LLM context
     */
    const getState = useCallback((): string => {
      const elements = Array.from(elementsMapRef.current.values());
      return JSON.stringify({
        elements: elements.map(elem => ({
          id: elem.id,
          type: elem.type,
          x: elem.x,
          y: elem.y,
          width: elem.width,
          height: elem.height,
          text: elem.text,
          strokeColor: elem.strokeColor,
          backgroundColor: elem.backgroundColor,
        })),
      });
    }, []);

    /**
     * Render operations to the canvas
     */
    const render = useCallback((operations: CanvasOperation[]) => {
      if (!excalidrawAPI || !operations?.length) return;

      const elements = operationsToExcalidrawElements(operations);
      excalidrawAPI.updateScene({ elements });
    }, [excalidrawAPI, operationsToExcalidrawElements]);

    /**
     * Clear the canvas
     */
    const clear = useCallback(() => {
      if (!excalidrawAPI) return;
      elementsMapRef.current.clear();
      excalidrawAPI.updateScene({ elements: [] });
    }, [excalidrawAPI]);

    useImperativeHandle(ref, () => ({ render, clear, getState }), [render, clear, getState]);

    return (
      <div className={className} style={{ width, height }}>
        <Excalidraw
          excalidrawAPI={(api) => setExcalidrawAPI(api as any)}
          onChange={(elements) => handleChange(elements)}
          initialData={{
            elements: [],
            appState: {
              viewBackgroundColor: "#ffffff",
              currentItemStrokeColor: "#000000",
              currentItemBackgroundColor: "transparent",
              currentItemFillStyle: "solid",
              currentItemStrokeWidth: 2,
              currentItemRoughness: 1,
            },
          }}
          UIOptions={{
            canvasActions: {
              loadScene: false,
            },
          }}
        />
      </div>
    );
  }
);

ExcalidrawCanvas.displayName = "ExcalidrawCanvas";
