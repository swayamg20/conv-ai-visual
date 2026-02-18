// Type definitions for Excalidraw integration
// These are simplified versions of the Excalidraw types we need

export interface ExcalidrawElement {
  id: string;
  type: string;
  x: number;
  y: number;
  width: number;
  height: number;
  strokeColor?: string;
  backgroundColor?: string;
  fillStyle?: string;
  strokeWidth?: number;
  roughness?: number;
  opacity?: number;
  angle?: number;
  roundness?: any;
  seed?: number;
  version?: number;
  versionNonce?: number;
  isDeleted?: boolean;
  groupIds?: string[];
  boundElements?: any[];
  updated?: number;
  link?: string | null;
  locked?: boolean;
  // Text-specific
  text?: string;
  fontSize?: number;
  fontFamily?: number;
  textAlign?: string;
  verticalAlign?: string;
  // Line/Arrow specific
  points?: number[][];
  lastCommittedPoint?: number[] | null;
  startBinding?: any;
  endBinding?: any;
  startArrowhead?: any;
  endArrowhead?: any;
}

export interface ExcalidrawImperativeAPI {
  updateScene: (sceneData: { elements?: ExcalidrawElement[]; appState?: any }) => void;
  resetScene: () => void;
  getSceneElements: () => ExcalidrawElement[];
  getAppState: () => any;
  getFiles: () => any;
  refresh: () => void;
  scrollToContent: (target?: ExcalidrawElement | readonly ExcalidrawElement[], opts?: any) => void;
  getSceneElementsIncludingDeleted: () => ExcalidrawElement[];
  history: {
    clear: () => void;
  };
  setToast: (toast: { message: string; closable?: boolean; duration?: number }) => void;
}

export interface ExcalidrawElementSkeleton {
  type: "rectangle" | "ellipse" | "diamond" | "line" | "arrow" | "text" | "frame" | "freedraw";
  id?: string;
  x?: number;
  y?: number;
  width?: number;
  height?: number;
  strokeColor?: string;
  backgroundColor?: string;
  strokeWidth?: number;
  roughness?: number;
  text?: string;
  fontSize?: number;
  fontFamily?: number;
  label?: { text: string };
}
