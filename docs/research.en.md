# Research workspace and enhanced reading

These features are ported from [PaperRead](https://github.com/YuriyLin/PaperRead), source revision `22ccc94`, onto EasyRead's current reader and library. Existing web-article reading, citation navigation, translated figures, offline exports and desktop data-directory behavior are retained.

## Getting started

```bash
python -m pip install -e .
easyread serve
```

Choose **Research** from the library, or open `/research`. Open `/web/tabs.html` for a browser workspace containing the library, research tools and multiple papers. Tabs support reordering, grouping and split reading.

AI actions use your existing CLI or API models configured under **Settings → Models**. No model account or API key is included. Paper discovery uses public services such as OpenAlex. Saved reading material, notes and plans remain available when those services are offline.

## Capabilities

- **Research workspace:** discover papers, maintain candidates, import PDFs, organize a reading board and dates, and manage milestones and followed topics/authors in the progress view.
- **Reading paths:** generate and reorder a reading route, inspect citation relations and explore related papers. AI recommendations are separate from externally verified citation evidence.
- **Editable AI mind maps:** generate a map from a paper, edit nodes and save your changes. Editing and regeneration are separate actions; saved changes survive reopening the paper.
- **Handwriting and annotations:** pen, colors and widths, eraser, stickers, text boxes, lasso selection and groups, with persistence and undo/redo.
- **Deep analysis:** inspect the research problem, method, experiments, evidence and limitations; append a report to paper notes or export it. Verify AI conclusions against the source.
- **Cross-paper analysis:** retrieve passages, scan keywords, compare methods and explore research relationships. Review proposed knowledge-base changes before accepting them.
- **Enhanced reader:** multiple tabs, split panes, rich-text notes, source quotations, images and diagrams alongside EasyRead's existing PDF and web readers.
- **Obsidian and Markdown export:** choose a Vault in research settings and export analysis, maps, annotations, deep reports, comparisons and summaries as linked Markdown files or a ZIP. Existing Markdown and offline HTML exports remain available.

Long-running analysis, maps and comparisons appear in the task monitor. You can cancel work, open completed results and retry failures. Restarted services mark interrupted tasks rather than presenting them as completed.

## Language and input modes

The UI defaults to Simplified Chinese and supports English and Traditional Chinese. Paper-specific AI annotations, maps, deep analysis and note assistance follow the paper target language, falling back to the UI when unspecified. Research summaries, reading paths and comparisons capture the UI language when started. English search terms/paraphrases are explicitly English-output tools. Source quotations and user-authored content are never silently converted. Saved results retain and display their actual generation language.

Handwriting supports mouse/touch or pen-only input. Pen-only mode accepts browser Pointer Events marked `pen`, suppresses accidental nonpen and compatibility-mouse input during contact, and releases guards when tools close. The sticker library opens independently without enabling handwriting.

Comparisons are generated and saved in the local library without requiring a Vault; Obsidian export is a separate action. Multi-content ZIP downloads also work without a Vault. Saved summaries have a Markdown editor, deep reports can be appended to paper notes, and scanned keyword relations have an editable map view.

## Smart annotation anchoring

Acceptance revalidates against current content and reader edits, not just the location flag saved during generation. If translation changes, a verified original quote or valid sentence alignment can preserve the annotation. When the translated sentence cannot be identified reliably, the mark stays on the original side; use bilingual/original view rather than guessing another translation.

AI Markdown, links, math and whitespace are matched using the reader’s visible text without rewriting saved quotations. Repeated acceptance does not recreate deleted notes or overwrite manually edited note bodies/colors. An unresolved quote now receives a neutral location warning. Older records without a reliable original anchor, or with ambiguous matches, still need review or regeneration.

## Semantic retrieval

Cross-paper semantic retrieval uses FastEmbed and `intfloat/multilingual-e5-small`. Its pinned model revision is downloaded on first use (approximately 250 MB), then cached below `.research-os/` in the EasyRead data directory. Indexes are scoped to the library and Vault so one library's results do not leak into another.

## One-way Zotero sync

Save a local Zotero data directory and import newly available PDFs on subsequent syncs. Sync does not modify Zotero, replace existing EasyRead tags/notes/translations, or automatically restore papers you previously deleted from EasyRead. Explicitly select previously deleted papers in the preview to reimport them.

## Data and export behavior

- Existing EasyRead home directories and translation-language preferences are not moved or rewritten.
- Notes, ink and stickers coexist with old reader fields in each paper's `reader.json`. Plans use a separate SQLite database in the library; original PDFs are not modified.
- Obsidian exports use stable IDs and managed-block markers. Re-export updates managed content while retaining text you add outside that block. Unrelated files are not silently replaced; review reported conflicts.
- A Vault path is a directory on the **server running EasyRead**, not the remote browser's computer.
- Offline HTML keeps saved reading material and annotations. New AI generation, online discovery and writing to a server Vault require the service. Editable ink strokes cannot be represented directly in Markdown; check the format warning when exporting.

## Attribution

Ported code remains MIT-licensed, retaining EasyRead and PaperRead attribution. Mind Elixir, editor dependencies and sticker assets retain their own licenses. Some stickers are independently licensed under CC BY-SA 4.0; see `easyread/web/stickers/license-manifest.json` and the accompanying license texts. Their licenses are distinct from the application's MIT code license.
