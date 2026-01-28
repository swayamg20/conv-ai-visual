"use client";

import { useState, useEffect, useImperativeHandle, forwardRef, useCallback, useRef } from "react";
import dynamic from "next/dynamic";
import type { CanvasOperation } from "@/hooks/use-webrtc";
import type { ExcalidrawElement, ExcalidrawImperativeAPI } from "@/types/excalidraw";
// Note: convertToExcalidrawElements is imported dynamically to avoid SSR issues

// Dynamically import Excalidraw with SSR disabled (Next.js requirement)
const Excalidraw = dynamic(
  async () => (await import("@excalidraw/excalidraw")).Excalidraw,
  { ssr: false }
);

export interface ExcalidrawCanvasHandle {
  render: (operations: CanvasOperation[]) => void;
  clear: () => void;
  getState: () => string; // Export canvas state as JSON for LLM context
  exportToPNG: () => Promise<void>; // Export canvas to PNG and download
  zoomToFit: () => void; // Zoom to fit all content
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
    const operationsToExcalidrawElements = useCallback(async (operations: CanvasOperation[]): Promise<ExcalidrawElement[]> => {
      const skeletonElements: any[] = [];

      // Check if this is a full scene update (no clear/update/delete actions = full redraw)
      const hasControlActions = operations.some(op =>
        ["clear", "update", "delete", "highlight"].includes(op.action)
      );

      // If it's a new diagram (no control actions), clear existing AI elements
      // but keep user-drawn elements (those without our el_ prefix)
      if (!hasControlActions && operations.length > 0) {
        const userElements = new Map<string, ExcalidrawElement>();
        elementsMapRef.current.forEach((elem, id) => {
          // Keep elements that don't have our prefix (user-drawn)
          if (!id.startsWith("el_")) {
            userElements.set(id, elem);
          }
        });
        elementsMapRef.current.clear();
        userElements.forEach((elem, id) => elementsMapRef.current.set(id, elem));
      }

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
            const rectId = op.id || `el_rect_${rectX}_${rectY}`;

            skeleton = {
              id: rectId,
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

            // If rect has label/text, add centered text element with stable ID
            const labelText = op.label || op.text;
            if (labelText) {
              const fontSize = op.font_size ?? 16;
              // Center text inside rectangle
              const textX = rectX + rectWidth / 2 - (labelText.length * fontSize * 0.3);
              const textY = rectY + rectHeight / 2 - fontSize / 2;

              skeletonElements.push({
                id: `${rectId}_label`,
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
            const circleId = op.id || `el_circle_${circleX}_${circleY}`;

            skeleton = {
              id: circleId,
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

            // If has label, add centered text with stable ID
            const circleLabel = op.label || op.text;
            if (circleLabel) {
              const fontSize = op.font_size ?? 16;
              const textX = circleX + circleSize / 2 - (circleLabel.length * fontSize * 0.3);
              const textY = circleY + circleSize / 2 - fontSize / 2;

              skeletonElements.push({
                id: `${circleId}_label`,
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
            const ellipseId = op.id || `el_ellipse_${ellipseX}_${ellipseY}`;

            skeleton = {
              id: ellipseId,
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

            // If has label, add centered text with stable ID
            const ellipseLabel = op.label || op.text;
            if (ellipseLabel) {
              const fontSize = op.font_size ?? 16;
              const textX = ellipseX + ellipseWidth / 2 - (ellipseLabel.length * fontSize * 0.3);
              const textY = ellipseY + ellipseHeight / 2 - fontSize / 2;

              skeletonElements.push({
                id: `${ellipseId}_label`,
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

          case "text": {
            const textX = op.x ?? 0;
            const textY = op.y ?? 0;
            skeleton = {
              id: op.id || `el_text_${textX}_${textY}`,
              type: "text",
              x: textX,
              y: textY,
              text: op.text ?? "",
              fontSize: op.font_size ?? 16,
              fontFamily: 1, // Excalidraw uses numeric font families
              textAlign: "left",
              verticalAlign: "top",
              strokeColor: op.color ?? "#000000",
            };
            break;
          }

          case "arrow":
            if (op.points && op.points.length >= 2) {
              const start = op.points[0];
              const startX = start[0];
              const startY = start[1];
              const end = op.points[op.points.length - 1];

              // Convert absolute points to relative points (offsets from start)
              const relativePoints = op.points.map(p => [p[0] - startX, p[1] - startY]);

              skeleton = {
                id: op.id || `el_arrow_${startX}_${startY}_${end[0]}_${end[1]}`,
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
              const end = op.points[op.points.length - 1];

              // Convert absolute points to relative points (offsets from start)
              const relativePoints = op.points.map(p => [p[0] - startX, p[1] - startY]);

              skeleton = {
                id: op.id || `el_line_${startX}_${startY}_${end[0]}_${end[1]}`,
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
          skeletonElements.push(skeleton);
        }
      }

      // Convert skeletons to Excalidraw elements
      if (skeletonElements.length === 0) {
        return Array.from(elementsMapRef.current.values());
      }

      // Dynamically import to avoid SSR issues
      const { convertToExcalidrawElements } = await import("@excalidraw/excalidraw");
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
     * Export canvas to PNG and trigger download
     */
    const exportToPNG = useCallback(async (): Promise<void> => {
      if (!excalidrawAPI) return;

      const elements = excalidrawAPI.getSceneElements();
      if (elements.length === 0) {
        console.warn("[Canvas] No elements to export");
        return;
      }

      try {
        // Dynamically import to avoid SSR issues
        const { exportToBlob } = await import("@excalidraw/excalidraw");

        const blob = await exportToBlob({
          elements: elements as any,
          appState: {
            exportWithDarkMode: false,
            exportBackground: true,
            viewBackgroundColor: "#ffffff",
          },
          files: excalidrawAPI.getFiles(),
          getDimensions: () => ({ width: 1600, height: 1200, scale: 2 }),
        });

        // Create download link
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = `canvas-${Date.now()}.png`;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(url);

        console.log("[Canvas] Exported to PNG");
      } catch (err) {
        console.error("[Canvas] Export failed:", err);
      }
    }, [excalidrawAPI]);

    /**
     * Zoom to fit all content in view
     */
    const zoomToFit = useCallback((): void => {
      if (!excalidrawAPI) return;

      const elements = excalidrawAPI.getSceneElements();
      if (elements.length === 0) return;

      excalidrawAPI.scrollToContent(elements as any, {
        fitToContent: true,
        viewportZoomFactor: 0.9, // Leave some padding
      });
    }, [excalidrawAPI]);

    /**
     * Render operations to the canvas
     */
    const render = useCallback(async (operations: CanvasOperation[]) => {
      if (!excalidrawAPI || !operations?.length) return;

      const elements = await operationsToExcalidrawElements(operations);
      excalidrawAPI.updateScene({ elements });

      // Auto-zoom to fit content after a small delay (let scene update)
      setTimeout(() => {
        if (elements.length > 0) {
          excalidrawAPI.scrollToContent(elements as any, {
            fitToContent: true,
            viewportZoomFactor: 0.85,
          });
        }
      }, 50);
    }, [excalidrawAPI, operationsToExcalidrawElements]);

    /**
     * Clear the canvas
     */
    const clear = useCallback(() => {
      if (!excalidrawAPI) return;
      elementsMapRef.current.clear();
      excalidrawAPI.updateScene({ elements: [] });
    }, [excalidrawAPI]);

    useImperativeHandle(ref, () => ({ render, clear, getState, exportToPNG, zoomToFit }), [render, clear, getState, exportToPNG, zoomToFit]);

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
