"use client";

import { useRef, useEffect, useImperativeHandle, forwardRef, useCallback } from "react";
import type { CanvasOperation } from "@/hooks/use-webrtc";

export interface CanvasRendererHandle {
  render: (operations: CanvasOperation[]) => void;
  clear: () => void;
}

interface CanvasRendererProps {
  width?: number;
  height?: number;
  className?: string;
}

export const CanvasRenderer = forwardRef<CanvasRendererHandle, CanvasRendererProps>(
  ({ width = 800, height = 600, className }, ref) => {
    const canvasRef = useRef<HTMLCanvasElement>(null);
    const elementsRef = useRef<Map<string, CanvasOperation>>(new Map());

    const getContext = useCallback(() => {
      return canvasRef.current?.getContext("2d") ?? null;
    }, []);

    const renderOperation = useCallback((ctx: CanvasRenderingContext2D, op: CanvasOperation) => {
      if (op.id && op.action !== "clear" && op.action !== "delete") {
        elementsRef.current.set(op.id, op);
      }

      ctx.save();
      ctx.strokeStyle = op.color ?? "#6366f1";
      ctx.fillStyle = op.fill ?? "transparent";
      ctx.lineWidth = op.stroke_width ?? 2;
      ctx.lineCap = "round";
      ctx.lineJoin = "round";

      switch (op.action) {
        case "rect":
          if (op.fill) ctx.fillRect(op.x!, op.y!, op.width!, op.height!);
          ctx.strokeRect(op.x!, op.y!, op.width!, op.height!);
          break;

        case "circle": {
          ctx.beginPath();
          const r = op.width! / 2;
          ctx.arc(op.x! + r, op.y! + r, r, 0, Math.PI * 2);
          if (op.fill) ctx.fill();
          ctx.stroke();
          break;
        }

        case "ellipse":
          ctx.beginPath();
          ctx.ellipse(
            op.x! + op.width! / 2,
            op.y! + op.height! / 2,
            op.width! / 2,
            op.height! / 2,
            0,
            0,
            Math.PI * 2
          );
          if (op.fill) ctx.fill();
          ctx.stroke();
          break;

        case "line":
          if (op.points?.length && op.points.length >= 2) {
            ctx.beginPath();
            ctx.moveTo(op.points[0][0], op.points[0][1]);
            for (let i = 1; i < op.points.length; i++) {
              ctx.lineTo(op.points[i][0], op.points[i][1]);
            }
            ctx.stroke();
          }
          break;

        case "arrow":
          if (op.points?.length && op.points.length >= 2) {
            const start = op.points[0];
            const end = op.points[op.points.length - 1];
            ctx.beginPath();
            ctx.moveTo(start[0], start[1]);
            for (let i = 1; i < op.points.length; i++) {
              ctx.lineTo(op.points[i][0], op.points[i][1]);
            }
            ctx.stroke();
            
            const angle = Math.atan2(end[1] - start[1], end[0] - start[0]);
            const len = 12;
            ctx.beginPath();
            ctx.moveTo(end[0], end[1]);
            ctx.lineTo(
              end[0] - len * Math.cos(angle - Math.PI / 6),
              end[1] - len * Math.sin(angle - Math.PI / 6)
            );
            ctx.moveTo(end[0], end[1]);
            ctx.lineTo(
              end[0] - len * Math.cos(angle + Math.PI / 6),
              end[1] - len * Math.sin(angle + Math.PI / 6)
            );
            ctx.stroke();
          }
          break;

        case "text":
          ctx.font = `${op.font_size ?? 16}px ${op.font_family ?? "DM Sans"}`;
          ctx.fillStyle = op.color ?? "#1a1a1d";
          ctx.textBaseline = "top";
          ctx.fillText(op.text ?? "", op.x!, op.y!);
          break;

        case "path":
          if (op.points?.length && op.points.length >= 2) {
            ctx.beginPath();
            ctx.moveTo(op.points[0][0], op.points[0][1]);
            for (let i = 1; i < op.points.length; i++) {
              ctx.lineTo(op.points[i][0], op.points[i][1]);
            }
            ctx.stroke();
          }
          break;

        case "delete":
          if (op.id || op.target_id) {
            elementsRef.current.delete(op.id ?? op.target_id!);
          }
          break;

        case "highlight": {
          const elem = elementsRef.current.get(op.id ?? op.target_id!);
          if (elem) {
            ctx.shadowColor = "#fbbf24";
            ctx.shadowBlur = 15;
            ctx.strokeStyle = "#fbbf24";
            ctx.lineWidth = 3;
            renderOperation(ctx, { ...elem });
          }
          break;
        }
      }

      ctx.restore();
    }, []);

    const redraw = useCallback(() => {
      const ctx = getContext();
      if (!ctx || !canvasRef.current) return;
      
      ctx.clearRect(0, 0, canvasRef.current.width, canvasRef.current.height);
      elementsRef.current.forEach((elem) => {
        renderOperation(ctx, elem);
      });
    }, [getContext, renderOperation]);

    const clear = useCallback(() => {
      const ctx = getContext();
      if (!ctx || !canvasRef.current) return;
      
      ctx.clearRect(0, 0, canvasRef.current.width, canvasRef.current.height);
      elementsRef.current.clear();
    }, [getContext]);

    const render = useCallback((operations: CanvasOperation[]) => {
      const ctx = getContext();
      if (!ctx || !operations?.length) return;

      for (const op of operations) {
        if (op.action === "clear") {
          clear();
        } else if (op.action === "delete") {
          if (op.id || op.target_id) {
            elementsRef.current.delete(op.id ?? op.target_id!);
            redraw();
          }
        } else {
          renderOperation(ctx, op);
        }
      }
    }, [getContext, clear, redraw, renderOperation]);

    useImperativeHandle(ref, () => ({ render, clear }), [render, clear]);

    return (
      <canvas
        ref={canvasRef}
        width={width}
        height={height}
        className={className}
      />
    );
  }
);

CanvasRenderer.displayName = "CanvasRenderer";

