import {
  Binary,
  BookOpen,
  File,
  FileArchive,
  FileCode,
  FileImage,
  FileJson,
  FileSpreadsheet,
  FileTerminal,
  FileText,
  FileType,
  Folder,
  FolderOpen,
  Settings,
  type LucideIcon,
} from "lucide-react";
import type { Artifact } from "./skillsApi";

/**
 * Icon + accent for an artifact, drawn from the same `lucide-react` set the rest
 * of the admin shell uses. Emoji were avoided deliberately: they render as
 * monochrome outline glyphs on the dark theme and ignore the palette.
 */
interface IconSpec {
  Icon: LucideIcon;
  tone: string;
}

const CODE_EXTENSIONS = new Set(["py", "pyi", "js", "mjs", "cjs", "jsx", "ts", "tsx", "java", "go", "rs", "rb", "php", "c", "h", "cpp", "hpp", "cs", "kt", "swift", "lua", "r"]);
const DOC_EXTENSIONS = new Set(["md", "markdown", "mdx", "txt", "rst", "log"]);
const DATA_EXTENSIONS = new Set(["json", "jsonc", "yaml", "yml", "toml", "ini", "cfg", "env", "csv", "tsv"]);
const SHEET_EXTENSIONS = new Set(["csv", "tsv", "xlsx", "xls"]);
const IMAGE_EXTENSIONS = new Set(["png", "jpg", "jpeg", "gif", "webp", "svg", "ico", "bmp", "tiff", "avif"]);
const ARCHIVE_EXTENSIONS = new Set(["zip", "gz", "tar", "tgz", "bz2", "xz", "7z", "rar"]);
const SHELL_EXTENSIONS = new Set(["sh", "bash", "zsh", "fish", "ps1", "bat", "cmd"]);
const MARKUP_EXTENSIONS = new Set(["html", "htm", "xml", "xsd", "xsl", "css", "scss", "less", "vue", "svelte"]);

/** Colour tokens come from the theme so icons follow every palette switch. */
const TONES = {
  folder: "var(--warning-color)",
  code: "var(--syntax-function, var(--info-color))",
  doc: "var(--text-secondary)",
  data: "var(--syntax-number, var(--warning-color))",
  image: "var(--syntax-string, var(--success-color))",
  archive: "var(--text-muted)",
  shell: "var(--syntax-keyword, var(--text-secondary))",
  markup: "var(--syntax-keyword, var(--info-color))",
  binary: "var(--text-muted)",
  skill: "var(--info-color)",
};

function extensionOf(path: string): string {
  const name = path.split("/").pop() || "";
  if (!name.includes(".")) return "";
  return name.split(".").pop()!.toLowerCase();
}

export function artifactIcon(item: Pick<Artifact, "path" | "type" | "binary" | "is_skill_md">, expanded = false): IconSpec {
  if (item.type === "dir") {
    return { Icon: expanded ? FolderOpen : Folder, tone: TONES.folder };
  }
  if (item.is_skill_md) return { Icon: BookOpen, tone: TONES.skill };
  if (item.binary) {
    const ext = extensionOf(item.path);
    if (IMAGE_EXTENSIONS.has(ext)) return { Icon: FileImage, tone: TONES.image };
    if (ARCHIVE_EXTENSIONS.has(ext)) return { Icon: FileArchive, tone: TONES.archive };
    return { Icon: Binary, tone: TONES.binary };
  }
  const ext = extensionOf(item.path);
  if (SHEET_EXTENSIONS.has(ext)) return { Icon: FileSpreadsheet, tone: TONES.data };
  if (CODE_EXTENSIONS.has(ext)) return { Icon: FileCode, tone: TONES.code };
  if (DATA_EXTENSIONS.has(ext)) return { Icon: FileJson, tone: TONES.data };
  if (IMAGE_EXTENSIONS.has(ext)) return { Icon: FileImage, tone: TONES.image };
  if (ARCHIVE_EXTENSIONS.has(ext)) return { Icon: FileArchive, tone: TONES.archive };
  if (SHELL_EXTENSIONS.has(ext)) return { Icon: FileTerminal, tone: TONES.shell };
  if (MARKUP_EXTENSIONS.has(ext)) return { Icon: FileType, tone: TONES.markup };
  if (DOC_EXTENSIONS.has(ext)) return { Icon: FileText, tone: TONES.doc };
  if (["dockerfile", "makefile", "containerfile"].includes((item.path.split("/").pop() || "").toLowerCase())) {
    return { Icon: Settings, tone: TONES.data };
  }
  return { Icon: File, tone: TONES.doc };
}

/** Small helper so callers can render the icon without re-deriving the spec. */
export function ArtifactIcon({
  item,
  expanded = false,
  size = 14,
}: {
  item: Pick<Artifact, "path" | "type" | "binary" | "is_skill_md">;
  expanded?: boolean;
  size?: number;
}) {
  const { Icon, tone } = artifactIcon(item, expanded);
  return <Icon size={size} strokeWidth={1.8} color={tone} aria-hidden="true" />;
}
