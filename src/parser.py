"""
Parser robusto de arquivo de coleta de ambiente Oracle/DB.

Tolerante a:
- variações de cabeçalho (#### SERVIDOR, ## BANCO ##, BACKUP'S/BACKUPS)
- ausência de cabeçalhos em tabelas (Date|TotalUsage|MonthGrown|Percentage)
- valores em MB / GB / TB / KB
- linhas com encoding latin-1 corrompido
- seções faltantes (ASM, archives, etc.)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NUM_RE = re.compile(r"[-+]?\d[\d,\.]*")
_UNIT_RE = re.compile(r"\b(KB|MB|GB|TB|PB|K|M|G|T|P)\b", re.I)

_UNIT_TO_GB = {
    "KB": 1 / (1024 * 1024),
    "K":  1 / (1024 * 1024),
    "MB": 1 / 1024,
    "M":  1 / 1024,
    "GB": 1.0,
    "G":  1.0,
    "TB": 1024.0,
    "T":  1024.0,
    "PB": 1024.0 * 1024.0,
    "P":  1024.0 * 1024.0,
}


def to_gb(value: float, unit: str) -> float:
    return value * _UNIT_TO_GB.get(unit.upper(), 1.0)


def parse_size(text: str, default_unit: str = "GB") -> Optional[float]:
    """Extrai um tamanho como GB. Aceita '32 Gb', '506 GB', '12,500M', '1.2T'."""
    if text is None:
        return None
    t = text.strip()
    if not t:
        return None
    num_match = _NUM_RE.search(t)
    if not num_match:
        return None
    raw = num_match.group(0).replace(",", "")
    try:
        value = float(raw)
    except ValueError:
        return None
    unit_match = _UNIT_RE.search(t[num_match.end():])
    unit = unit_match.group(1) if unit_match else default_unit
    return to_gb(value, unit)


def clean_value(line: str, key: str) -> str:
    """Remove a chave 'Foo:' do início da linha e devolve o valor."""
    pattern = re.compile(re.escape(key) + r"\s*:?\s*", re.I)
    return pattern.sub("", line, count=1).strip()


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ServerInfo:
    fabricante: str = ""
    modelo: str = ""
    service_tag: str = ""
    qtd_proc: str = ""
    modelo_proc: str = ""
    multi_proc: str = ""
    memoria_total_gb: Optional[float] = None
    memoria_usada_gb: Optional[float] = None
    memoria_livre_gb: Optional[float] = None
    hostname: str = ""
    ip: str = ""
    so: str = ""
    versao_banco: str = ""

    def memoria_label(self) -> str:
        if self.memoria_total_gb:
            return f"{self.memoria_total_gb:,.0f} GB"
        return "—"


@dataclass
class Filesystem:
    device: str
    fs_type: str
    size_gb: float
    used_gb: float
    avail_gb: float
    use_pct: float
    mount: str

    @property
    def status(self) -> str:
        if self.use_pct >= 90:
            return "CRÍTICO"
        if self.use_pct >= 75:
            return "ATENÇÃO"
        return "OK"

    @property
    def free_gb(self) -> float:
        return max(0.0, self.size_gb - self.used_gb)


@dataclass
class ASMDiskGroup:
    name: str
    total_gb: float
    usable_free_gb: float
    used_gb: float
    pct_used: float
    redundancy: str = ""


@dataclass
class GrowthEntry:
    period: str           # "Jan2025"
    total_gb: float       # tamanho da base no fim do período
    growth_gb: float      # crescimento absoluto no período
    pct: Optional[float]  # % vs base original (do arquivo)


@dataclass
class Datafile:
    fid: str
    tablespace: str
    filename: str
    pct: float
    mb_used: float
    mb_max: float


@dataclass
class Instance:
    nome: str = ""
    tipo: str = ""
    db_size_gb: Optional[float] = None
    db_used_gb: Optional[float] = None
    db_free_gb: Optional[float] = None
    logico_total_gb: Optional[float] = None
    crescimento: list[GrowthEntry] = field(default_factory=list)
    archives_daily_gb: list[float] = field(default_factory=list)  # GB/dia (somatório DD)
    archives_location: str = ""
    datafiles: list[Datafile] = field(default_factory=list)
    raw_text: str = ""
    aberto: bool = True   # False quando aparece "não aberto"

    # ---------- métricas ----------
    @property
    def crescimento_meses_validos(self) -> list[float]:
        """Crescimentos mensais ignorando zero, negativos e None.
        Considera apenas o último ano de histórico (até 12 meses)."""
        return [e.growth_gb for e in self.crescimento[-12:]
                if e.growth_gb is not None and e.growth_gb > 0]

    @property
    def media_crescimento_mensal_gb(self) -> float:
        """Média do crescimento mensal da BASE (datafiles).
        Soma os crescimentos válidos / quantidade de meses válidos."""
        valid = self.crescimento_meses_validos
        if not valid:
            return 0.0
        return sum(valid) / len(valid)

    @property
    def qtd_meses_considerados(self) -> int:
        return len(self.crescimento_meses_validos)

    @property
    def media_archive_diaria_gb(self) -> float:
        valid = [v for v in self.archives_daily_gb if v is not None and v > 0]
        if not valid:
            return 0.0
        return sum(valid) / len(valid)

    @property
    def media_archive_mensal_gb(self) -> float:
        """Geração mensal de archives (média diária × 30 dias)."""
        return self.media_archive_diaria_gb * 30

    @property
    def pct_crescimento_mensal(self) -> float:
        """% de crescimento mensal sobre o tamanho atual da base."""
        base = self.db_size_gb or 0
        if base <= 0:
            return 0.0
        return (self.media_crescimento_mensal_gb / base) * 100

    # --- compat (mantido p/ outras telas) ---
    @property
    def crescimento_archive_mensal_gb(self) -> float:
        return self.media_archive_mensal_gb

    @property
    def crescimento_total_mensal_gb(self) -> float:
        """Crescimento real esperado da BASE por mês.
        Archives são gerados, copiados em backup e apagados —
        não somam ao tamanho da base no longo prazo, apenas pressionam
        o filesystem de archives temporariamente."""
        return self.media_crescimento_mensal_gb

    def projecao_base_gb(self, meses: int) -> float:
        """Projeção da BASE (datafiles) considerando média mensal × meses."""
        base = self.db_size_gb or 0.0
        return base + self.media_crescimento_mensal_gb * meses

    def projecao_archives_residente_gb(self, dias_retencao: int = 1) -> float:
        """Volume médio de archives presente no FS de archives
        (gerado e ainda não apagado pelo backup). Default: 1 dia."""
        return self.media_archive_diaria_gb * max(1, dias_retencao)

    def projecao_backup_mensal_gb(self) -> float:
        """Crescimento mensal estimado de área de backup:
        base atual cresce por mês + archives gerados no mês."""
        return self.media_crescimento_mensal_gb + self.media_archive_mensal_gb

    # alias antigo
    def projecao_gb(self, meses: int) -> float:
        return self.projecao_base_gb(meses)

    # ---------- distribuição de datafiles por filesystem / ASM DG ----------
    def datafiles_por_mount(self, mounts: list[str]) -> dict[str, float]:
        """Agrupa o tamanho (GB) dos datafiles pelo destino físico:
        - paths começando com '+' são tratados como ASM (chave 'ASM +DG')
        - paths POSIX casam com o mount mais específico (mais longo)
        - sem match → '(desconhecido)'.
        """
        ordered = sorted([m for m in mounts if m and m != "/"],
                         key=len, reverse=True)
        has_root = "/" in mounts
        out: dict[str, float] = {}
        for d in self.datafiles:
            gb = (d.mb_used or 0) / 1024.0
            if gb <= 0:
                gb = (d.mb_max or 0) / 1024.0
            path = (d.filename or "").strip()
            chosen: Optional[str] = None
            if path.startswith("+"):
                dg = path.split("/", 1)[0]   # ex: '+DATAC1'
                chosen = f"ASM {dg}"
            else:
                for m in ordered:
                    pref = m.rstrip("/") + "/"
                    if path.startswith(pref) or path == m:
                        chosen = m
                        break
                if chosen is None and has_root and path.startswith("/"):
                    chosen = "/"
            if chosen is None:
                chosen = "(desconhecido)"
            out[chosen] = out.get(chosen, 0.0) + gb
        return out


@dataclass
class Backup:
    tipo: str = ""
    diretorio: str = ""
    tamanho_gb: Optional[float] = None
    horario_inicio: str = ""
    duracao: str = ""
    instancia: str = ""   # tentativa de relacionar com instância
    contexto: str = ""    # ex: "exa03adm01vm01 - rub1"


@dataclass
class CollectReport:
    server: ServerInfo = field(default_factory=ServerInfo)
    filesystems: list[Filesystem] = field(default_factory=list)
    asm: list[ASMDiskGroup] = field(default_factory=list)
    instances: list[Instance] = field(default_factory=list)
    backups: list[Backup] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Section splitting
# ---------------------------------------------------------------------------

_SECTION_HEADER = re.compile(r"^#{4,}\s*(SERVIDOR|BANCO|BACKUP[´'`]?S?)\s*#{0,}", re.I)


def split_sections(text: str) -> list[tuple[str, list[str]]]:
    """Divide o texto em (tipo_secao, linhas). Tipos: SERVIDOR, BANCO, BACKUPS."""
    lines = text.splitlines()
    sections: list[tuple[str, list[str]]] = []
    current_type: Optional[str] = None
    current_lines: list[str] = []
    for line in lines:
        m = _SECTION_HEADER.match(line.strip())
        if m:
            if current_type is not None:
                sections.append((current_type, current_lines))
            kind = m.group(1).upper()
            if kind.startswith("BACKUP"):
                kind = "BACKUPS"
            current_type = kind
            current_lines = []
        else:
            if current_type is None:
                current_type = "PREAMBULO"
                current_lines = []
            current_lines.append(line)
    if current_type is not None:
        sections.append((current_type, current_lines))
    return sections


# ---------------------------------------------------------------------------
# SERVIDOR / FILESYSTEM / ASM
# ---------------------------------------------------------------------------

_SERVER_FIELDS = {
    "fabricante": ["fabricante"],
    "modelo": ["modelo"],
    "service_tag": ["service tag"],
    "qtd_proc": ["qtd. proc", "qtd proc"],
    "modelo_proc": ["modelo. proc", "modelo proc"],
    "multi_proc": ["multi-processamento", "multi processamento"],
    "hostname": ["hostname"],
    "ip": ["ip"],
    "so": ["s.o", "so", "sistema operacional"],
    "versao_banco": ["versão banco", "versao banco", "versão", "versao"],
}


def _match_field(line: str, keys: list[str]) -> Optional[str]:
    low = line.lower()
    for k in keys:
        idx = low.find(k + ":")
        if idx == -1:
            # também aceita "k :" com espaço
            idx = low.find(k)
            if idx == -1:
                continue
            after = low[idx + len(k):].lstrip()
            if not after.startswith(":"):
                continue
            value_start = low.find(":", idx) + 1
            return line[value_start:].strip()
        return line[idx + len(k) + 1:].strip()
    return None


def parse_server_block(lines: list[str], report: CollectReport) -> None:
    server = report.server
    text = "\n".join(lines)

    for attr, keys in _SERVER_FIELDS.items():
        for line in lines:
            value = _match_field(line, keys)
            if value:
                setattr(server, attr, value.strip())
                break

    # memória — tenta achar bloco "free -g"
    mem_match = re.search(
        r"Mem:\s*(\d+)\s+(\d+)\s+(\d+)", text)
    if mem_match:
        server.memoria_total_gb = float(mem_match.group(1))
        server.memoria_usada_gb = float(mem_match.group(2))
        server.memoria_livre_gb = float(mem_match.group(3))
    else:
        # fallback: linha "Memória: 512 GB"
        for line in lines:
            if re.match(r"\s*mem(ó|o)ria\s*:", line, re.I):
                size = parse_size(line.split(":", 1)[1] if ":" in line else "")
                if size:
                    server.memoria_total_gb = size
                    break

    # filesystem (df -h)
    parse_filesystems(text, report)

    # ASM
    parse_asm(text, report)


_DF_LINE = re.compile(
    r"^(?P<dev>\S+)\s+(?P<type>[a-zA-Z0-9]+)\s+"
    r"(?P<size>[\d\.,]+[KMGT]?)\s+(?P<used>[\d\.,]+[KMGT]?)\s+"
    r"(?P<avail>[\d\.,]+[KMGT]?)\s+(?P<pct>\d+)%\s+(?P<mount>\S.*)$"
)


def _df_size_to_gb(token: str) -> float:
    token = token.strip()
    if not token:
        return 0.0
    m = re.match(r"([\d\.,]+)([KMGTP]?)", token, re.I)
    if not m:
        return 0.0
    value = float(m.group(1).replace(",", "."))
    unit = (m.group(2) or "G").upper()
    return to_gb(value, unit)


def parse_filesystems(text: str, report: CollectReport) -> None:
    seen_mounts: set[str] = set()
    for line in text.splitlines():
        m = _DF_LINE.match(line.strip())
        if not m:
            continue
        mount = m.group("mount").strip()
        if mount in {"on", "Mounted"}:
            continue
        if mount in seen_mounts:
            continue
        seen_mounts.add(mount)
        try:
            fs = Filesystem(
                device=m.group("dev"),
                fs_type=m.group("type"),
                size_gb=_df_size_to_gb(m.group("size")),
                used_gb=_df_size_to_gb(m.group("used")),
                avail_gb=_df_size_to_gb(m.group("avail")),
                use_pct=float(m.group("pct")),
                mount=mount,
            )
            report.filesystems.append(fs)
        except Exception as exc:
            report.warnings.append(f"Filesystem inválido: {line!r} ({exc})")


_ASM_LINE = re.compile(
    r"^(?P<name>[A-Z0-9_]+)\s*\|\s*(?P<total>\d+)\s*\|\s*(?P<free>\d+)\s*\|"
    r"\s*(?P<used>\d+)\s*\|\s*(?P<req>\d+)\s*\|\s*(?P<pct>\d+)\s*\|\s*(?P<type>\w+)"
)


def parse_asm(text: str, report: CollectReport) -> None:
    in_asm = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("ASM:"):
            in_asm = True
            continue
        if not in_asm:
            continue
        if stripped.startswith("Hostname") or stripped.startswith("IP") or stripped.startswith("S.O"):
            in_asm = False
            continue
        m = _ASM_LINE.match(stripped)
        if m:
            report.asm.append(ASMDiskGroup(
                name=m.group("name"),
                total_gb=float(m.group("total")),
                usable_free_gb=float(m.group("free")),
                used_gb=float(m.group("used")),
                pct_used=float(m.group("pct")),
                redundancy=m.group("type"),
            ))


# ---------------------------------------------------------------------------
# BANCO
# ---------------------------------------------------------------------------

_DBSIZE_RE = re.compile(
    r"(\d[\d,\.]*)\s*(KB|MB|GB|TB)\s*\|\s*(\d[\d,\.]*)\s*(KB|MB|GB|TB)\s*\|\s*"
    r"(\d[\d,\.]*)\s*(KB|MB|GB|TB)", re.I,
)

_GROWTH_RE = re.compile(
    r"^([A-Z][a-z]{2}\d{4})\s*\|\s*([+-]?[\d,\.]+)\s*([KMGTP]?)"
    r"\s*\|\s*([+-]?[\d,\.]+)\s*([KMGTP]?)\s*\|\s*([+-]?[\d,\.]+)%?",
    re.I,
)

_DD_LINE_RE = re.compile(r"^\s*DD\s*=\s*\[(.*)\]\s*$")
_DATAFILE_RE = re.compile(
    r"^\s*(\d+)\s*\|\s*(\S+)\s*\|\s*(\S+)\s*\|\s*(\d+)\s*\|\s*([\d,]+)\s*\|\s*([\d,]+)\s*$"
)


def _parse_growth_block(lines: list[str]) -> list[GrowthEntry]:
    entries: list[GrowthEntry] = []
    for line in lines:
        s = line.strip()
        if not s or s.lower().startswith("date"):
            continue
        m = _GROWTH_RE.match(s)
        if not m:
            continue
        period = m.group(1)
        total_val = float(m.group(2).replace(",", ""))
        total_gb = to_gb(total_val, m.group(3) or "G")
        growth_val = float(m.group(4).replace(",", ""))
        growth_gb = to_gb(growth_val, m.group(5) or "M")
        try:
            pct = float(m.group(6))
        except ValueError:
            pct = None
        entries.append(GrowthEntry(period=period, total_gb=total_gb,
                                   growth_gb=growth_gb, pct=pct))
    return entries


def _parse_archive_dd(lines: list[str]) -> list[float]:
    """Encontra a linha 'DD = [ x | y | ... ]' e devolve totais por dia em GB."""
    for line in lines:
        m = _DD_LINE_RE.match(line)
        if not m:
            continue
        cells = [c.strip() for c in m.group(1).split("|")]
        out: list[float] = []
        for c in cells:
            if not c or set(c) <= {"-"}:
                continue
            num = re.match(r"([\d,\.]+)\s*([KMGT]?)", c, re.I)
            if not num:
                continue
            value = float(num.group(1).replace(",", ""))
            unit = num.group(2) or "M"
            out.append(to_gb(value, unit))
        return out
    return []


def _parse_datafiles(lines: list[str]) -> list[Datafile]:
    out: list[Datafile] = []
    for line in lines:
        m = _DATAFILE_RE.match(line)
        if not m:
            continue
        out.append(Datafile(
            fid=m.group(1),
            tablespace=m.group(2),
            filename=m.group(3),
            pct=float(m.group(4)),
            mb_used=float(m.group(5).replace(",", "")),
            mb_max=float(m.group(6).replace(",", "")),
        ))
    return out


def parse_banco_block(lines: list[str]) -> Optional[Instance]:
    inst = Instance(raw_text="\n".join(lines))

    # Nome
    for line in lines[:5]:
        if re.search(r"nome\s+inst", line, re.I):
            value = line.split(":", 1)[1].strip() if ":" in line else ""
            # remover comentários como "- não aberto: ..."
            inst.nome = re.split(r"\s+-\s+", value, maxsplit=1)[0].strip()
            if "não aberto" in value.lower() or "nao aberto" in value.lower():
                inst.aberto = False
            break

    if not inst.nome:
        return None

    text = "\n".join(lines)

    # Tipo
    m = re.search(r"^Tipo\s*:\s*(.+)$", text, re.I | re.M)
    if m:
        inst.tipo = m.group(1).strip()

    # Tamanho banco físico (Database Size | Used | Free)
    db = _DBSIZE_RE.search(text)
    if db:
        inst.db_size_gb = to_gb(float(db.group(1).replace(",", "")), db.group(2))
        inst.db_used_gb = to_gb(float(db.group(3).replace(",", "")), db.group(4))
        inst.db_free_gb = to_gb(float(db.group(5).replace(",", "")), db.group(6))

    # Tamanho banco lógico (Total)
    m = re.search(r"^\s*([\d,\.]+)\s*([KMGT])\s*\|\s*Total", text, re.I | re.M)
    if m:
        inst.logico_total_gb = to_gb(
            float(m.group(1).replace(",", "")), m.group(2))

    # Crescimento
    inst.crescimento = _parse_growth_block(lines)

    # Archive DD
    inst.archives_daily_gb = _parse_archive_dd(lines)

    # Local archives
    m = re.search(r"location\s*=\s*(\S+)", text, re.I)
    if m:
        inst.archives_location = m.group(1)
    else:
        m = re.search(r"Archive destination\s+(\S+)", text, re.I)
        if m:
            inst.archives_location = m.group(1)

    # Datafiles
    inst.datafiles = _parse_datafiles(lines)

    return inst


# ---------------------------------------------------------------------------
# BACKUPS
# ---------------------------------------------------------------------------

def parse_backups_block(lines: list[str]) -> list[Backup]:
    backups: list[Backup] = []
    current: Optional[Backup] = None

    for raw in lines:
        line = raw.strip()
        if not line:
            if current and current.tipo:
                backups.append(current)
                current = None
            continue
        low = line.lower()
        if low.startswith("tipos de backup"):
            continue
        if low.startswith("tipo:") or low.startswith("tipo :"):
            if current and current.tipo:
                backups.append(current)
            current = Backup()
            value = line.split(":", 1)[1].strip()
            ctx_match = re.search(r"\((.+?)\)", value)
            if ctx_match:
                current.contexto = ctx_match.group(1).strip()
                value = re.sub(r"\(.+?\)", "", value).strip()
            current.tipo = value
            continue
        if current is None:
            continue
        if low.startswith("diret"):
            current.diretorio = line.split(":", 1)[1].strip()
        elif low.startswith("tamanho"):
            current.tamanho_gb = parse_size(line.split(":", 1)[1])
        elif low.startswith("hor"):
            current.horario_inicio = line.split(":", 1)[1].strip()
        elif low.startswith("dura"):
            current.duracao = line.split(":", 1)[1].strip()

    if current and current.tipo:
        backups.append(current)
    return backups


def relate_backups(report: CollectReport) -> None:
    inst_names = [i.nome.lower() for i in report.instances if i.nome]
    for b in report.backups:
        target = (b.contexto + " " + b.diretorio).lower()
        best = ""
        for name in inst_names:
            # tenta nome completo, depois raiz (parte antes de "_")
            for cand in {name, name.split("_")[0]}:
                if cand and cand in target and len(cand) > len(best):
                    best = name
        b.instancia = best


# ---------------------------------------------------------------------------
# Top-level API
# ---------------------------------------------------------------------------

def read_text(path: str | Path) -> str:
    raw = Path(path).read_bytes()
    for enc in ("utf-8", "latin-1", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def parse_collect(path: str | Path) -> CollectReport:
    text = read_text(path)
    report = CollectReport()
    for kind, block_lines in split_sections(text):
        if kind == "SERVIDOR":
            parse_server_block(block_lines, report)
        elif kind == "BANCO":
            inst = parse_banco_block(block_lines)
            if inst:
                report.instances.append(inst)
        elif kind == "BACKUPS":
            report.backups.extend(parse_backups_block(block_lines))
    relate_backups(report)
    return report


# ---------------------------------------------------------------------------
# Aggregations
# ---------------------------------------------------------------------------

def crescimento_total_mensal(report: CollectReport) -> float:
    return sum(i.crescimento_total_mensal_gb for i in report.instances)


def filesystem_meses_ate_lotar(fs: Filesystem, growth_per_month_gb: float) -> Optional[float]:
    if growth_per_month_gb <= 0:
        return None
    return fs.free_gb / growth_per_month_gb


def alertas(report: CollectReport) -> list[dict]:
    out: list[dict] = []
    for fs in report.filesystems:
        if fs.use_pct >= 90:
            out.append({"nivel": "CRÍTICO",
                        "msg": f"Filesystem {fs.mount} em {fs.use_pct:.0f}% de uso"})
        elif fs.use_pct >= 75:
            out.append({"nivel": "ATENÇÃO",
                        "msg": f"Filesystem {fs.mount} em {fs.use_pct:.0f}% de uso"})
    # crescimento acelerado
    for inst in report.instances:
        if inst.media_crescimento_mensal_gb > 1000:
            out.append({"nivel": "ATENÇÃO",
                        "msg": f"Instância {inst.nome} cresce {inst.media_crescimento_mensal_gb:,.0f} GB/mês"})
    # falta de backup (considera raiz/CDB — PDBs do mesmo CDB compartilham backup)
    backed_roots = set()
    for b in report.backups:
        if b.instancia:
            backed_roots.add(b.instancia.lower())
            backed_roots.add(b.instancia.lower().split("_")[0])
    for inst in report.instances:
        if not inst.aberto:
            continue
        nome = inst.nome.lower()
        if nome in backed_roots or nome.split("_")[0] in backed_roots:
            continue
        out.append({"nivel": "ATENÇÃO",
                    "msg": f"Sem backup identificado para a instância {inst.nome}"})
    return out


def crescimento_base_mensal(report: CollectReport) -> float:
    """Crescimento líquido das bases (sem archives) — mais realista para projeção."""
    return sum(i.media_crescimento_mensal_gb for i in report.instances)


def archive_total_diario_gb(report: CollectReport) -> float:
    return sum(i.media_archive_diaria_gb for i in report.instances)
