# Markdown Viewer

Windows desktop Markdown viewer built with PyQt6.

## Images And Diagrams

Standard Markdown images render directly — local (relative or absolute) and remote URLs are all supported. Large images scale to fit the content width automatically.

```markdown
![Architecture](./images/arch.png)
![Logo](https://example.com/logo.png)
```

Diagrams can be written inline with [Mermaid](https://mermaid.js.org/) fenced code blocks and are rendered live (bundled offline — no network required):

````markdown
```mermaid
graph TD
  User --> Frontend
  Frontend --> API
  API --> Database
```
````

Mermaid diagrams re-color automatically when you switch between light and dark themes.

## Development

```powershell
py -3 -m pip install -r requirements.txt
py -3 main.py
```

## Build Installer

Install build tools first:

```powershell
py -3 -m pip install pyinstaller Pillow
```

Build the icon, executable, and installer:

```powershell
py -3 tools/build_icon.py
py -3 -m PyInstaller markdown_viewer.spec
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
```

The installer is written to `installer_output/`.

## Release And Auto Update

The application checks GitHub Releases for updates. A release must include an installer asset named like `MarkdownViewer_Setup_v1.2.0.exe`.

To publish a new version:

```powershell
py -3 tools/bump_version.py 1.2.1
git add .
git commit -m "Release v1.2.1"
git tag v1.2.1
git push
git push origin v1.2.1
```

The GitHub Actions release workflow builds the Windows installer and uploads it to the GitHub Release. Installed users can then use `Help > Check for Updates`.
