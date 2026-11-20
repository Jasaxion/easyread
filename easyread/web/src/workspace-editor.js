import { EditorState, Compartment } from '@codemirror/state';
import { EditorView, keymap, lineNumbers, highlightActiveLine, highlightActiveLineGutter, drawSelection, rectangularSelection } from '@codemirror/view';
import { defaultKeymap, history, historyKeymap, indentWithTab } from '@codemirror/commands';
import { StreamLanguage, syntaxHighlighting, defaultHighlightStyle, bracketMatching, foldGutter, foldKeymap, indentOnInput } from '@codemirror/language';
import { stex } from '@codemirror/legacy-modes/mode/stex';
import { autocompletion, completionKeymap, snippetCompletion, closeBrackets, closeBracketsKeymap } from '@codemirror/autocomplete';
import { setDiagnostics, lintGutter } from '@codemirror/lint';
import { searchKeymap, highlightSelectionMatches } from '@codemirror/search';
import { json } from '@codemirror/lang-json';
import { javascript } from '@codemirror/lang-javascript';
import { markdown } from '@codemirror/lang-markdown';
import { python } from '@codemirror/lang-python';

const commands = ['documentclass', 'usepackage', 'title', 'author', 'date', 'maketitle', 'tableofcontents', 'chapter', 'section', 'subsection', 'subsubsection', 'paragraph', 'label', 'ref', 'eqref', 'cite', 'bibliography', 'bibliographystyle', 'include', 'input', 'includegraphics', 'caption', 'footnote', 'textbf', 'textit', 'emph', 'frac', 'sqrt', 'sum', 'int', 'alpha', 'beta', 'gamma', 'delta', 'epsilon', 'theta', 'lambda', 'mu', 'pi', 'sigma', 'omega', 'mathbb', 'mathbf', 'mathrm', 'left', 'right', 'item', 'newcommand'];
const environments = ['document', 'abstract', 'figure', 'table', 'tabular', 'equation', 'align', 'itemize', 'enumerate', 'description', 'verbatim', 'center', 'matrix'];
function latexCompletion(files) {
  return context => {
    const before = context.state.sliceDoc(Math.max(0, context.pos - 250), context.pos);
    const file = /\\(?:input|include|includegraphics|bibliography|addbibresource)\{([^}]*)$/.exec(before);
    if (file) return { from: context.pos - file[1].length, options: files().map(label => ({ label, type: 'text' })) };
    const reference = /\\(?:ref|eqref)\{([^}]*)$/.exec(before);
    if (reference) {
      const labels = [...context.state.doc.toString().matchAll(/\\label\{([^}]+)\}/g)].map(m => ({ label: m[1], type: 'variable' }));
      return { from: context.pos - reference[1].length, options: labels };
    }
    const env = /\\(?:begin|end)\{([^}]*)$/.exec(before);
    if (env) return { from: context.pos - env[1].length, options: environments.map(label => ({ label, type: 'keyword' })) };
    const word = context.matchBefore(/\\[a-zA-Z]*/);
    if (!word) return null;
    return { from: word.from, options: [
      ...commands.map(name => ({ label: '\\' + name, type: 'function' })),
      ...environments.map(name => snippetCompletion('\\begin{' + name + '}\n\t${}\n\\end{' + name + '}', { label: '\\begin{' + name + '}', type: 'keyword' }))
    ] };
  };
}
function language(path, files) {
  if (/\.(tex|sty|cls|bib)$/i.test(path)) return [StreamLanguage.define(stex), autocompletion({ override: [latexCompletion(files)] })];
  if (/\.json$/i.test(path)) return [json(), autocompletion()];
  if (/\.(js|ts|jsx|tsx|mjs|cjs)$/i.test(path)) return [javascript({ typescript: /tsx?$/.test(path), jsx: /[jt]sx$/.test(path) }), autocompletion()];
  if (/\.py$/i.test(path)) return [python(), autocompletion()];
  if (/\.(md|markdown)$/i.test(path)) return [markdown(), autocompletion()];
  return [autocompletion()];
}
const theme = EditorView.theme({
  '&': { height: '100%', color: 'var(--ink)', backgroundColor: 'var(--surface)' },
  '.cm-scroller': { overflow: 'auto', fontFamily: 'ui-monospace, SFMono-Regular, Consolas, monospace', lineHeight: '1.75' },
  '.cm-content': { padding: '14px 0', caretColor: 'var(--ink)' },
  '.cm-gutters': { backgroundColor: 'var(--bg)', color: 'var(--ink-3)', borderColor: 'var(--rule)' },
  '.cm-activeLine, .cm-activeLineGutter': { backgroundColor: 'color-mix(in srgb, var(--accent) 7%, transparent)' },
  '.cm-tooltip, .cm-panels': { backgroundColor: 'var(--surface)', color: 'var(--ink)', borderColor: 'var(--rule)' },
  '.cm-cursor': { borderLeftColor: 'var(--ink)' },
  '&.cm-focused .cm-selectionBackground, .cm-selectionBackground': { backgroundColor: 'color-mix(in srgb, var(--accent) 22%, transparent)' },
  '.cm-searchMatch': { backgroundColor: '#e0a84f55' },
});
window.PR.workspaceEditor = {
  create(parent, options) {
    let replacing = false;
    const mode = new Compartment(), font = new Compartment(), editable = new Compartment();
    const fontTheme = size => EditorView.theme({ '.cm-scroller': { fontSize: Math.max(10, Math.min(22, Number(size) || 12)) + 'px' } });
    const view = new EditorView({ parent, state: EditorState.create({ doc: options.content || '', extensions: [
      lineNumbers(), history(), drawSelection(), rectangularSelection(), highlightActiveLine(), highlightActiveLineGutter(),
      syntaxHighlighting(defaultHighlightStyle), bracketMatching(), foldGutter(), indentOnInput(), closeBrackets(), lintGutter(), highlightSelectionMatches(),
      EditorView.lineWrapping, theme, font.of(fontTheme(options.fontSize || 14)), editable.of(EditorState.readOnly.of(false)), mode.of(language(options.path, options.files || (() => []))),
      EditorView.contentAttributes.of({ 'aria-label': window.PR.t('源码编辑器') }),
      keymap.of([{ key: 'Mod-Shift-Enter', run: () => options.onLocate ? (options.onLocate(), true) : false }, { key: 'Mod-Enter', run: () => options.onCompile ? (options.onCompile(), true) : false }, { key: 'Mod-s', run: () => { options.onSave(); return true; } }, indentWithTab, ...defaultKeymap, ...historyKeymap, ...searchKeymap, ...foldKeymap, ...closeBracketsKeymap, ...completionKeymap]),
      EditorView.updateListener.of(update => {
        if (update.docChanged && !replacing) options.onChange(update.state.doc.toString());
        if (update.selectionSet) options.onSelection?.(update.state.selection.main);
      })
    ] }) });
    return {
      content: () => view.state.doc.toString(),
      setFontSize: size => view.dispatch({ effects: font.reconfigure(fontTheme(size)) }),
      setReadOnly: value => view.dispatch({ effects: editable.reconfigure(EditorState.readOnly.of(value)) }),
      position: () => {
        const head = view.state.selection.main.head, line = view.state.doc.lineAt(head);
        return { line: line.number, column: head - line.from + 1 };
      },
      selection: () => ({ anchor: view.state.selection.main.anchor, head: view.state.selection.main.head, scroll: view.scrollDOM.scrollTop }),
      restore(selection) {
        if (!selection) return;
        const limit = view.state.doc.length;
        view.dispatch({ selection: { anchor: Math.min(limit, selection.anchor || 0), head: Math.min(limit, selection.head || 0) } });
        requestAnimationFrame(() => { view.scrollDOM.scrollTop = selection.scroll || 0; });
      },
      replace(content) {
        replacing = true;
        view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: content } });
        replacing = false;
      },
      gotoLine(number, column = 1) {
        const line = view.state.doc.line(Math.max(1, Math.min(view.state.doc.lines, Number(number) || 1)));
        const anchor = Math.min(line.to, line.from + Math.max(0, (Number(column) || 1) - 1));
        view.dispatch({ selection: { anchor }, effects: EditorView.scrollIntoView(anchor, { y: 'center' }) }); view.focus();
      },
      diagnostics(errors) {
        view.dispatch(setDiagnostics(view.state, errors.map(error => {
          const line = view.state.doc.line(Math.max(1, Math.min(view.state.doc.lines, Number(error.line) || 1)));
          return { from: line.from, to: line.to, severity: 'error', message: error.message || '' };
        })));
      },
      focus: () => view.focus(), destroy: () => view.destroy()
    };
  }
};
