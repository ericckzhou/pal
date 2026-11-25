import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import rehypeHighlight from "rehype-highlight";
import "katex/dist/katex.min.css";

// Models often write \( … \) and \[ … \]; remark-math only knows $ … $ and $$ … $$.
// Rewrite those delimiters outside code spans/fences so both styles render.
function normalizeMath(text: string): string {
  return text
    .split(/(```[\s\S]*?```|`[^`\n]*`)/g)
    .map((part, i) =>
      i % 2 === 1
        ? part
        : part
            .replace(/\\\[([\s\S]+?)\\\]/g, (_, m: string) => `\n$$\n${m.trim()}\n$$\n`)
            .replace(/\\\(([\s\S]+?)\\\)/g, (_, m: string) => `$${m.trim()}$`),
    )
    .join("");
}

// S5: inert rendering. Raw HTML is skipped and images are dropped, so nothing loads remotely.
// Math is rendered locally by KaTeX (CSS and fonts are bundled), so it loads nothing remote either.
// Citation handles the reply was given (e.g. [d1]) become small in-page links to the source list.
export function Markdown({ text, cites, anchor = "" }: { text: string; cites?: Set<string>; anchor?: string }) {
  // Handles the model invented (not in this reply's sources) are dropped rather than shown as fake citations.
  const cited = cites
    ? text.replace(/\s?\[([mdcnp]\d+)\](?!\()/g, (whole, h: string) =>
        cites.has(h) ? whole.replace(`[${h}]`, `[${h}](#src-${anchor}${h})`) : "",
      )
    : text;
  const body = normalizeMath(cited);
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm, remarkMath]}
      rehypePlugins={[[rehypeKatex, { throwOnError: false, strict: "ignore" }], [rehypeHighlight, { detect: false }]]}
      skipHtml
      disallowedElements={["img"]}
      unwrapDisallowed
      components={{
        a: ({ href, children }) =>
          href?.startsWith("#src-") ? (
            <a className="cite" href={href}>
              {children}
            </a>
          ) : (
            <a href={href} target="_blank" rel="noopener noreferrer">
              {children}
            </a>
          ),
      }}
    >
      {body}
    </ReactMarkdown>
  );
}

// Search snippets mark matches with « »; render them as <mark> without any HTML parsing.
export function Snippet({ text }: { text: string }) {
  const parts = text.split(/(«[^»]*»)/g);
  return (
    <>
      {parts.map((p, i) => (p.startsWith("«") ? <mark key={i}>{p.slice(1, -1)}</mark> : <span key={i}>{p}</span>))}
    </>
  );
}
