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
          case "rect": {
            const rectWidth = op.width ?? 100;
            const rectHeight = op.height ?? 50;
            const rectX = op.x ?? 0;
            const rectY = op.y ?? 0;
            
            skeleton = {
              type: "rectangle",
              x: rectX,
              y: rectY,
              width: rectWidth,
              height: rectHeight,
              strokeColor: op.color ?? "#000000",
              backgroundColor: op.fill ?? "transparent",
              strokeWidth: op.stroke_width ?? 2,
              roughness: 1,
              roundness: null,
            };
            
            // If rect has label/text, add centered text element
            const labelText = op.label || op.text;
            if (labelText) {
              const fontSize = op.font_size ?? 16;
              // Center text inside rectangle
              const textX = rectX + rectWidth / 2 - (labelText.length * fontSize * 0.3);
              const textY = rectY + rectHeight / 2 - fontSize / 2;
              
              skeletonElements.push({
                type: "text",
                x: textX,
                y: textY,
                text: labelText,
                fontSize: fontSize,
                fontFamily: 1,
                textAlign: "center",
                verticalAlign: "middle",
                strokeColor: op.color ?? "#000000",
              });
            }
            break;
          }

          case "circle": {
            const circleSize = op.width ?? 100;
            const circleX = op.x ?? 0;
            const circleY = op.y ?? 0;
            
            skeleton = {
              type: "ellipse",
              x: circleX,
              y: circleY,
              width: circleSize,
              height: circleSize,
              strokeColor: op.color ?? "#000000",
              backgroundColor: op.fill ?? "transparent",
              strokeWidth: op.stroke_width ?? 2,
              roughness: 1,
            };
            
            // If has label, add centered text
            const circleLabel = op.label || op.text;
            if (circleLabel) {
              const fontSize = op.font_size ?? 16;
              const textX = circleX + circleSize / 2 - (circleLabel.length * fontSize * 0.3);
              const textY = circleY + circleSize / 2 - fontSize / 2;
              
              skeletonElements.push({
                type: "text",
                x: textX,
                y: textY,
                text: circleLabel,
                fontSize: fontSize,
                fontFamily: 1,
                textAlign: "center",
                verticalAlign: "middle",
                strokeColor: op.color ?? "#000000",
              });
            }
            break;
          }

          case "ellipse": {
            const ellipseWidth = op.width ?? 100;
            const ellipseHeight = op.height ?? 80;
            const ellipseX = op.x ?? 0;
            const ellipseY = op.y ?? 0;
            
            skeleton = {
              type: "ellipse",
              x: ellipseX,
              y: ellipseY,
              width: ellipseWidth,
              height: ellipseHeight,
              strokeColor: op.color ?? "#000000",
              backgroundColor: op.fill ?? "transparent",
              strokeWidth: op.stroke_width ?? 2,
              roughness: 1,
            };
            
            // If has label, add centered text
            const ellipseLabel = op.label || op.text;
            if (ellipseLabel) {
              const fontSize = op.font_size ?? 16;
              const textX = ellipseX + ellipseWidth / 2 - (ellipseLabel.length * fontSize * 0.3);
              const textY = ellipseY + ellipseHeight / 2 - fontSize / 2;
              
              skeletonElements.push({
                type: "text",
                x: textX,
                y: textY,
                text: ellipseLabel,
                fontSize: fontSize,
                fontFamily: 1,
                textAlign: "center",
                verticalAlign: "middle",
                strokeColor: op.color ?? "#000000",
              });
            }
            break;
          }

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
              const startX = start[0];
              const startY = start[1];

              // Convert absolute points to relative points (offsets from start)
              const relativePoints = op.points.map(p => [p[0] - startX, p[1] - startY]);

              skeleton = {
                type: "arrow",
                x: startX,
                y: startY,
                points: relativePoints,
                strokeColor: op.color ?? "#000000",
                strokeWidth: op.stroke_width ?? 2,
                roughness: 1,
              };
            }
            break;

          case "line":
            if (op.points && op.points.length >= 2) {
              const start = op.points[0];
              const startX = start[0];
              const startY = start[1];

              // Convert absolute points to relative points (offsets from start)
              const relativePoints = op.points.map(p => [p[0] - startX, p[1] - startY]);

              skeleton = {
                type: "line",
                x: startX,
                y: startY,
                points: relativePoints,
                strokeColor: op.color ?? "#000000",
                strokeWidth: op.stroke_width ?? 2,
                roughness: 1,
              };
            }
            break;

          case "path":
            // Convert path to freedraw (Excalidraw's freehand drawing type)
            if (op.points && op.points.length >= 2) {
              const start = op.points[0];
              const startX = start[0];
              const startY = start[1];

              // Convert absolute points to relative points (offsets from start)
              const relativePoints = op.points.map(p => [p[0] - startX, p[1] - startY]);

              skeleton = {
                type: "freedraw",
                x: startX,
                y: startY,
                points: relativePoints,
                strokeColor: op.color ?? "#000000",
                strokeWidth: op.stroke_width ?? 2,
                roughness: 0, // Freedraw should be smooth
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
