const SVG_NAMESPACE = "http://www.w3.org/2000/svg";

function triggerDownload(url: string, filename: string): void {
  const link = document.createElement("a");
  link.download = filename;
  link.href = url;
  link.click();
}

function collectKatexStyles(): string {
  let styles = "";
  try {
    for (const styleSheet of Array.from(document.styleSheets)) {
      if (!styleSheet.href?.includes("katex")) continue;
      for (const rule of Array.from(styleSheet.cssRules)) styles += `${rule.cssText}\n`;
    }
  } catch {
    // Cross-origin stylesheets may not expose their rules.
  }
  return styles;
}

/** Save the current canvas as PNG, or SVG when KaTeX foreign objects are present. */
export function saveCanvasImage(svg: SVGSVGElement, width: number, height: number): void {
  const clone = svg.cloneNode(true) as SVGSVGElement;
  clone.setAttribute("xmlns", SVG_NAMESPACE);
  clone.setAttribute("xmlns:xlink", "http://www.w3.org/1999/xlink");
  clone.setAttribute("viewBox", `0 0 ${width} ${height}`);

  const background = document.createElementNS(SVG_NAMESPACE, "rect");
  background.setAttribute("width", String(width));
  background.setAttribute("height", String(height));
  background.setAttribute("fill", "#ffffff");
  clone.insertBefore(background, clone.firstChild);

  const katexStyles = collectKatexStyles();
  if (katexStyles) {
    const style = document.createElementNS(SVG_NAMESPACE, "style");
    style.textContent = katexStyles;
    clone.insertBefore(style, clone.firstChild);
  }

  const blob = new Blob([new XMLSerializer().serializeToString(clone)], {
    type: "image/svg+xml;charset=utf-8",
  });
  const url = URL.createObjectURL(blob);
  const timestamp = new Date().toISOString().slice(0, 19).replace(/:/g, "-");

  if (clone.querySelector("foreignObject")) {
    triggerDownload(url, `canvas_${timestamp}.svg`);
    window.setTimeout(() => URL.revokeObjectURL(url), 100);
    return;
  }

  const image = new Image();
  image.onerror = () => URL.revokeObjectURL(url);
  image.onload = () => {
    const canvas = document.createElement("canvas");
    const scale = 2;
    canvas.width = width * scale;
    canvas.height = height * scale;
    const context = canvas.getContext("2d");
    if (!context) {
      URL.revokeObjectURL(url);
      return;
    }
    context.scale(scale, scale);
    context.fillStyle = "#ffffff";
    context.fillRect(0, 0, width, height);
    context.drawImage(image, 0, 0, width, height);
    canvas.toBlob((pngBlob) => {
      if (!pngBlob) return;
      const pngUrl = URL.createObjectURL(pngBlob);
      triggerDownload(pngUrl, `canvas_${timestamp}.png`);
      URL.revokeObjectURL(pngUrl);
    }, "image/png");
    URL.revokeObjectURL(url);
  };
  image.src = url;
}
