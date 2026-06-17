import { marked } from 'marked';
import markedKatex from 'marked-katex-extension';

let richTextConfigured = false;

function ensureRichTextConfigured() {
  if (richTextConfigured) {
    return;
  }

  marked.use(
    markedKatex({
      throwOnError: false,
      output: 'html'
    })
  );

  richTextConfigured = true;
}

function normalizeMarkdownContent(content: string) {
  return content
    .replace(/\r/g, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

function normalizeSimpleInlineSubscripts(content: string) {
  return content.replace(
    /\$([A-Za-z]+)_\{?([A-Za-z0-9]+)\}?\$/g,
    (_match, base: string, subscript: string) =>
      `<span class="inline-subscript">${base}<sub>${subscript}</sub></span>`
  );
}

function isSafeHref(value: string) {
  const trimmed = value.trim();
  if (!trimmed) {
    return false;
  }

  if (trimmed.startsWith('#') || trimmed.startsWith('/')) {
    return true;
  }

  try {
    const url = new URL(trimmed, window.location.origin);
    return ['http:', 'https:', 'mailto:', 'tel:'].includes(url.protocol);
  } catch {
    return false;
  }
}

function isSafeImageSrc(value: string) {
  const trimmed = value.trim();
  if (!trimmed) {
    return false;
  }

  if (trimmed.startsWith('/') || trimmed.startsWith('#')) {
    return true;
  }

  try {
    const url = new URL(trimmed, window.location.origin);
    return ['http:', 'https:'].includes(url.protocol) || trimmed.startsWith('data:image/');
  } catch {
    return false;
  }
}

function sanitizeRenderedHtml(html: string) {
  const parser = new DOMParser();
  const document = parser.parseFromString(html, 'text/html');
  const unsafeTags = new Set([
    'script',
    'style',
    'iframe',
    'object',
    'embed',
    'link',
    'meta',
    'form',
    'input',
    'button',
    'textarea',
    'select',
    'option',
    'svg',
    'math',
    'canvas',
    'video',
    'audio'
  ]);
  const allowedTags = new Set([
    'A',
    'ABBR',
    'B',
    'BLOCKQUOTE',
    'BR',
    'CODE',
    'DEL',
    'DIV',
    'EM',
    'H1',
    'H2',
    'H3',
    'H4',
    'H5',
    'H6',
    'HR',
    'I',
    'IMG',
    'LI',
    'OL',
    'P',
    'PRE',
    'S',
    'SMALL',
    'SPAN',
    'STRONG',
    'SUB',
    'SUP',
    'TABLE',
    'TBODY',
    'TD',
    'TH',
    'THEAD',
    'TR',
    'UL'
  ]);

  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT);
  const nodes: Element[] = [];

  while (walker.nextNode()) {
    nodes.push(walker.currentNode as Element);
  }

  for (const node of nodes) {
    const tagName = node.tagName.toUpperCase();

    if (unsafeTags.has(tagName)) {
      node.remove();
      continue;
    }

    if (!allowedTags.has(tagName)) {
      node.replaceWith(...Array.from(node.childNodes));
      continue;
    }

    for (const attr of Array.from(node.attributes)) {
      const name = attr.name.toLowerCase();
      const value = attr.value;

      if (name.startsWith('on') || name === 'style') {
        node.removeAttribute(attr.name);
        continue;
      }

      if (tagName === 'A' && name === 'href' && !isSafeHref(value)) {
        node.removeAttribute(attr.name);
        continue;
      }

      if (tagName === 'IMG' && name === 'src' && !isSafeImageSrc(value)) {
        node.removeAttribute(attr.name);
        continue;
      }

      if (tagName !== 'A' && tagName !== 'IMG' && name !== 'class') {
        node.removeAttribute(attr.name);
      }
    }

    if (tagName === 'A') {
      node.setAttribute('rel', 'noreferrer noopener');
      if (node.getAttribute('target')) {
        node.removeAttribute('target');
      }
    }
  }

  return document.body.innerHTML;
}

export function renderRichText(content: string) {
  ensureRichTextConfigured();

  return sanitizeRenderedHtml(
    marked.parse(
      normalizeSimpleInlineSubscripts(normalizeMarkdownContent(String(content ?? '')))
    ) as string
  );
}

export function renderRichTextInline(content: string) {
  ensureRichTextConfigured();

  return sanitizeRenderedHtml(
    marked.parseInline(
      normalizeSimpleInlineSubscripts(normalizeMarkdownContent(String(content ?? ''))),
      { async: false }
    ) as string
  );
}
