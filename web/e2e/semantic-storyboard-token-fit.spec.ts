import { readFileSync } from "node:fs";
import path from "node:path";

import { expect, test } from "@playwright/test";

interface TokenCase {
  readonly id: string;
  readonly latex: string;
  readonly fontSize: number;
  readonly width: number;
  readonly height: number;
}

const katexDistPath = path.resolve("node_modules/katex/dist");
const katexCssPath = path.join(katexDistPath, "katex.min.css");
const katexCss = readFileSync(katexCssPath, "utf8").replace(
  /url\(fonts\/([^)]*\.woff2)\)/g,
  (_match, filename: string) => {
    const font = readFileSync(
      path.join(path.dirname(katexCssPath), "fonts", filename),
    );
    return `url(data:font/woff2;base64,${font.toString("base64")})`;
  },
);

function formatValue(value: number): string {
  return (Math.round((value + Number.EPSILON) * 100) / 100)
    .toFixed(2)
    .replace(/\.?0+$/, "");
}

function cases(): readonly TokenCase[] {
  const result: TokenCase[] = [];
  const pairs = [
    [30, 45],
    [30, 60],
    [45, 60],
  ] as const;
  for (const speed of [20, 25, 30] as const) {
    for (const [lower, higher] of pairs) {
      const prefix = `${speed}-${lower}-${higher}`;
      const complementary = lower + higher === 90;
      const rawRange = (angle: number) =>
        (speed ** 2 * Math.sin((2 * angle * Math.PI) / 180)) / 10;
      const lowerRange = rawRange(lower);
      const higherRange = complementary ? lowerRange : rawRange(higher);
      const height = (angle: number) =>
        (speed ** 2 * Math.sin((angle * Math.PI) / 180) ** 2) / 20;
      const flight = (angle: number) =>
        (2 * speed * Math.sin((angle * Math.PI) / 180)) / 10;
      const symbol = complementary ? "=" : lowerRange < higherRange ? "<" : ">";
      result.push(
        {
          id: `${prefix}-lower-angle`,
          latex: `${lower}^\\circ`,
          fontSize: 22,
          width: 58,
          height: 36,
        },
        {
          id: `${prefix}-higher-angle`,
          latex: `${higher}^\\circ`,
          fontSize: 22,
          width: 58,
          height: 36,
        },
        {
          id: `${prefix}-givens`,
          latex: `\\begin{aligned}v_0&=${speed}\\,\\mathrm{m/s}\\\\theta_L&=${lower}^\\circ,\\quad \\theta_H=${higher}^\\circ\\end{aligned}`,
          fontSize: 14,
          width: 188,
          height: 64,
        },
        {
          id: `${prefix}-range-formula`,
          latex: "R(\\theta)=\\frac{v_0^2}{g}\\sin(2\\theta)",
          fontSize: 19,
          width: 188,
          height: 48,
        },
        {
          id: `${prefix}-sine-relation`,
          latex: `\\sin(${2 * lower}^\\circ)\\ ${symbol}\\ \\sin(${2 * higher}^\\circ)`,
          fontSize: 16,
          width: 188,
          height: 38,
        },
        {
          id: `${prefix}-lower-range`,
          latex: `R_L=${formatValue(lowerRange)}\\,\\mathrm{m}`,
          fontSize: 16,
          width: 126,
          height: 28,
        },
        {
          id: `${prefix}-higher-range`,
          latex: `R_H=${formatValue(higherRange)}\\,\\mathrm{m}`,
          fontSize: 16,
          width: 126,
          height: 28,
        },
        {
          id: `${prefix}-lower-height`,
          latex: `H_L=${formatValue(height(lower))}\\,\\mathrm{m}`,
          fontSize: 16,
          width: 126,
          height: 30,
        },
        {
          id: `${prefix}-higher-height`,
          latex: `H_H=${formatValue(height(higher))}\\,\\mathrm{m}`,
          fontSize: 16,
          width: 126,
          height: 30,
        },
        {
          id: `${prefix}-lower-flight`,
          latex: `T_L=${formatValue(flight(lower))}\\,\\mathrm{s}`,
          fontSize: 16,
          width: 188,
          height: 30,
        },
        {
          id: `${prefix}-higher-flight`,
          latex: `T_H=${formatValue(flight(higher))}\\,\\mathrm{s}`,
          fontSize: 16,
          width: 188,
          height: 30,
        },
        {
          id: `${prefix}-range-relation`,
          latex: `R_L\\ ${symbol}\\ R_H`,
          fontSize: 22,
          width: 188,
          height: 46,
        },
        {
          id: `${prefix}-height-relation`,
          latex: "H_H>H_L",
          fontSize: 22,
          width: 188,
          height: 38,
        },
        {
          id: `${prefix}-flight-relation`,
          latex: "T_H>T_L",
          fontSize: 16,
          width: 188,
          height: 30,
        },
      );
      if (complementary) {
        result.push({
          id: `${prefix}-complementary`,
          latex: `${lower}^\\circ+${higher}^\\circ=90^\\circ`,
          fontSize: 22,
          width: 188,
          height: 42,
        });
      }
    }
  }
  return result;
}

test("all server-owned storyboard tokens fit their declared frames in Chromium", async ({
  page,
}) => {
  await page.setContent('<main id="tokens"></main>');
  await page.addStyleTag({ content: katexCss });
  await page.addScriptTag({ path: path.join(katexDistPath, "katex.min.js") });

  for (const token of cases()) {
    await page.evaluate(({ height, id, fontSize, latex, width }) => {
      const katexApi = (
        window as unknown as {
          katex: {
            renderToString(
              source: string,
              options: { displayMode: boolean; throwOnError: boolean },
            ): string;
          };
        }
      ).katex;
      const container = document.createElement("div");
      container.dataset.tokenId = id;
      container.innerHTML = katexApi.renderToString(latex, {
        displayMode: false,
        throwOnError: true,
      });
      Object.assign(container.style, {
        alignItems: "center",
        display: "flex",
        fontSize: `${fontSize}px`,
        height: `${height}px`,
        justifyContent: "center",
        overflow: "hidden",
        whiteSpace: "nowrap",
        width: `${width}px`,
      });
      document.querySelector("#tokens")?.appendChild(container);
    }, token);
  }

  await page.evaluate(() => document.fonts.ready);
  for (const token of cases()) {
    const measured = await page
      .locator(`[data-token-id="${token.id}"] .katex`)
      .evaluate((rendered) => {
        const bounds = rendered.getBoundingClientRect();
        return { width: bounds.width, height: bounds.height };
      });
    expect(measured.width, `${token.id} width`).toBeLessThanOrEqual(
      token.width,
    );
    expect(measured.height, `${token.id} height`).toBeLessThanOrEqual(
      token.height,
    );
  }
});
