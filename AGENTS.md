# AniForge development

- Default rendering tests are exactly three views per affected model: front (0°), side (90°), back (180°). Reuse/crop these images for detail inspection instead of rendering extra comparison sheets or expression combinations.
- Do not run 36-image batches or the historical A/B suite unless the user explicitly requests a full dataset or full comparison. Ordinary tests must not render images implicitly.
- Keep imported/downloaded third-party add-ons, PMX/PMD models, textures, .blend scenes, local reports and render outputs out of Git. Commit source, build scripts, tests and project documentation.
- Preserve source model/image files. Material fixes belong in the add-on's nodes and copied Blender image datablocks.
