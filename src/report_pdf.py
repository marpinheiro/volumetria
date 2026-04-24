"""
Gerador de relatório executivo PDF.
Usa matplotlib (sem GUI) + reportlab Platypus.
"""
from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

from src.parser import (
    CollectReport, alertas, archive_total_diario_gb, crescimento_base_mensal,
    crescimento_total_mensal, filesystem_meses_ate_lotar,
)

# ---------------------------------------------------------------------------
# Style helpers
# ---------------------------------------------------------------------------

PRIMARY = colors.HexColor("#1f3a68")
ACCENT = colors.HexColor("#2e7dd1")
DANGER = colors.HexColor("#c0392b")
WARNING = colors.HexColor("#d68910")
OK = colors.HexColor("#1e8449")
LIGHT_BG = colors.HexColor("#eef3fa")


def _styles():
    base = getSampleStyleSheet()
    base.add(ParagraphStyle(
        name="ExecTitle", parent=base["Title"], fontSize=22,
        textColor=PRIMARY, spaceAfter=14, alignment=0,
    ))
    base.add(ParagraphStyle(
        name="H1Custom", parent=base["Heading1"], fontSize=15,
        textColor=PRIMARY, spaceBefore=10, spaceAfter=6,
    ))
    base.add(ParagraphStyle(
        name="H2Custom", parent=base["Heading2"], fontSize=12,
        textColor=ACCENT, spaceBefore=6, spaceAfter=4,
    ))
    base.add(ParagraphStyle(
        name="BodyJustify", parent=base["BodyText"], fontSize=10,
        leading=14, alignment=4,
    ))
    base.add(ParagraphStyle(
        name="Small", parent=base["BodyText"], fontSize=8.5, leading=11,
    ))
    return base


def _fmt_gb(v: float | None) -> str:
    if v is None:
        return "—"
    if v >= 1024:
        return f"{v/1024:,.2f} TB"
    return f"{v:,.1f} GB"


def _kpi_table(report: CollectReport):
    crit = sum(1 for fs in report.filesystems if fs.use_pct >= 90)
    warn = sum(1 for fs in report.filesystems if 75 <= fs.use_pct < 90)
    cresc_total = crescimento_total_mensal(report)
    rows = [
        ["Instâncias", "Filesystems", "Crítico", "Atenção", "Crescimento mensal"],
        [str(len(report.instances)), str(len(report.filesystems)),
         str(crit), str(warn), _fmt_gb(cresc_total)],
    ]
    t = Table(rows, colWidths=[3.4 * cm] * 5)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), PRIMARY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("FONTSIZE", (0, 1), (-1, 1), 13),
        ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BACKGROUND", (0, 1), (-1, 1), LIGHT_BG),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.grey),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
        ("ROWHEIGHT", (0, 0), (-1, -1), 18),
    ]))
    return t


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------

def _chart_growth(report: CollectReport) -> Image | None:
    instances = [i for i in report.instances if i.crescimento]
    instances.sort(key=lambda i: i.media_crescimento_mensal_gb, reverse=True)
    top = instances[:6]
    if not top:
        return None
    fig, ax = plt.subplots(figsize=(8, 4.2))
    for inst in top:
        labels = [g.period for g in inst.crescimento]
        vals = [g.total_gb for g in inst.crescimento]
        ax.plot(labels, vals, marker="o", linewidth=1.6, label=inst.nome)
    ax.set_title("Evolução do tamanho da base — Top instâncias", fontsize=12)
    ax.set_ylabel("Tamanho (GB)")
    ax.tick_params(axis="x", rotation=45, labelsize=8)
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=8, loc="upper left")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=140)
    plt.close(fig)
    buf.seek(0)
    return Image(buf, width=17 * cm, height=8.5 * cm)


def _chart_projection(report: CollectReport) -> Image | None:
    base_total = sum((i.db_size_gb or 0) for i in report.instances)
    growth_total = crescimento_base_mensal(report)
    if base_total <= 0:
        return None
    months = list(range(0, 37))
    series = [base_total + growth_total * m for m in months]
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.fill_between(months, series, alpha=0.25, color="#2e7dd1")
    ax.plot(months, series, color="#1f3a68", linewidth=2)
    for marker in (12, 24, 36):
        ax.axvline(marker, color="grey", linestyle="--", alpha=0.5)
        ax.annotate(f"{_fmt_gb(series[marker])}",
                    xy=(marker, series[marker]),
                    xytext=(4, 6), textcoords="offset points",
                    fontsize=8, color="#1f3a68")
    ax.set_title("Projeção total de armazenamento (todas as instâncias)", fontsize=12)
    ax.set_xlabel("Meses a partir de hoje")
    ax.set_ylabel("Tamanho total (GB)")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=140)
    plt.close(fig)
    buf.seek(0)
    return Image(buf, width=17 * cm, height=8 * cm)


def _chart_filesystem(report: CollectReport) -> Image | None:
    if not report.filesystems:
        return None
    fs_sorted = sorted(report.filesystems, key=lambda f: f.use_pct, reverse=True)
    names = [f.mount for f in fs_sorted]
    pcts = [f.use_pct for f in fs_sorted]
    cols = ["#c0392b" if p >= 90 else "#d68910" if p >= 75 else "#1e8449"
            for p in pcts]
    fig, ax = plt.subplots(figsize=(8, max(3.5, 0.35 * len(names))))
    bars = ax.barh(names, pcts, color=cols)
    ax.invert_yaxis()
    ax.set_xlim(0, 105)
    ax.set_xlabel("Uso (%)")
    ax.set_title("Ocupação por filesystem", fontsize=12)
    for bar, pct in zip(bars, pcts):
        ax.text(bar.get_width() + 1, bar.get_y() + bar.get_height() / 2,
                f"{pct:.0f}%", va="center", fontsize=8)
    ax.grid(True, axis="x", alpha=0.3)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=140)
    plt.close(fig)
    buf.seek(0)
    return Image(buf, width=17 * cm, height=min(14, 0.55 * len(names) + 2) * cm)


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------

def _server_block(report: CollectReport, st):
    s = report.server
    pwrap = ParagraphStyle("cell", fontSize=9, leading=11)
    def P(t): return Paragraph(t or "—", pwrap)
    rows = [
        [P("<b>Hostname</b>"), P(s.hostname), P("<b>IP</b>"), P(s.ip)],
        [P("<b>Fabricante</b>"), P(s.fabricante), P("<b>Modelo</b>"), P(s.modelo)],
        [P("<b>Service Tag</b>"), P(s.service_tag), P("<b>Processador</b>"), P(s.modelo_proc)],
        [P("<b>Qtd. Proc.</b>"), P(s.qtd_proc), P("<b>Memória total</b>"), P(s.memoria_label())],
        [P("<b>Sistema Operacional</b>"), P(s.so), P("<b>Versão Banco</b>"), P(s.versao_banco)],
    ]
    t = Table(rows, colWidths=[3.7 * cm, 5.5 * cm, 3.2 * cm, 5.4 * cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), LIGHT_BG),
        ("BACKGROUND", (2, 0), (2, -1), LIGHT_BG),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def _filesystem_table(report: CollectReport):
    # crescimento atribuído proporcionalmente apenas a filesystems "grandes" (≥100 GB)
    base_growth = crescimento_base_mensal(report)
    big = [f for f in report.filesystems if f.size_gb >= 100]
    big_total = sum(f.size_gb for f in big) or 1
    rows = [["Mount", "Tipo", "Tamanho", "Usado", "Livre", "%", "Status",
             "Meses\nestimados"]]
    for fs in sorted(report.filesystems, key=lambda f: f.use_pct, reverse=True):
        if fs in big and base_growth > 0:
            share = base_growth * (fs.size_gb / big_total)
            meses = filesystem_meses_ate_lotar(fs, share)
            meses_label = f"{meses:.0f}" if meses else "—"
        else:
            meses_label = "—"
        rows.append([fs.mount, fs.fs_type, _fmt_gb(fs.size_gb), _fmt_gb(fs.used_gb),
                     _fmt_gb(fs.free_gb), f"{fs.use_pct:.0f}%", fs.status, meses_label])
    t = Table(rows, repeatRows=1,
              colWidths=[3.4 * cm, 1.3 * cm, 2.1 * cm, 2.1 * cm,
                         2.1 * cm, 1.1 * cm, 1.9 * cm, 2.0 * cm])
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), PRIMARY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]
    for i, fs in enumerate(sorted(report.filesystems, key=lambda f: f.use_pct, reverse=True), start=1):
        if fs.use_pct >= 90:
            style.append(("BACKGROUND", (6, i), (6, i), DANGER))
            style.append(("TEXTCOLOR", (6, i), (6, i), colors.white))
        elif fs.use_pct >= 75:
            style.append(("BACKGROUND", (6, i), (6, i), WARNING))
            style.append(("TEXTCOLOR", (6, i), (6, i), colors.white))
        else:
            style.append(("BACKGROUND", (6, i), (6, i), OK))
            style.append(("TEXTCOLOR", (6, i), (6, i), colors.white))
    t.setStyle(TableStyle(style))
    return t


def _instances_table(report: CollectReport):
    rows = [["Instância", "Tipo", "Tamanho", "Cresc/mês", "Archive/dia",
             "Cresc total/mês", "12m", "24m", "36m"]]
    for i in sorted(report.instances, key=lambda x: x.db_size_gb or 0, reverse=True):
        rows.append([
            i.nome, i.tipo or "—",
            _fmt_gb(i.db_size_gb), _fmt_gb(i.media_crescimento_mensal_gb),
            _fmt_gb(i.media_archive_diaria_gb),
            _fmt_gb(i.crescimento_total_mensal_gb),
            _fmt_gb(i.projecao_gb(12)), _fmt_gb(i.projecao_gb(24)),
            _fmt_gb(i.projecao_gb(36)),
        ])
    t = Table(rows, repeatRows=1,
              colWidths=[3.4 * cm, 1.6 * cm, 1.9 * cm, 1.9 * cm, 1.9 * cm,
                         2.1 * cm, 1.9 * cm, 1.9 * cm, 1.9 * cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), PRIMARY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_BG]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return t


def _backups_table(report: CollectReport):
    if not report.backups:
        return Paragraph("Nenhum backup identificado.", _styles()["BodyText"])
    rows = [["Tipo", "Instância", "Diretório", "Tamanho", "Início", "Duração"]]
    for b in report.backups:
        rows.append([b.tipo, b.instancia or "—", b.diretorio,
                     _fmt_gb(b.tamanho_gb), b.horario_inicio, b.duracao])
    t = Table(rows, repeatRows=1,
              colWidths=[2.6 * cm, 2.8 * cm, 6 * cm, 2 * cm, 2 * cm, 2.2 * cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), PRIMARY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_BG]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return t


def _alertas_block(report: CollectReport):
    items = alertas(report)
    if not items:
        return [Paragraph("Nenhum alerta crítico identificado.", _styles()["BodyText"])]
    out = []
    for a in items:
        color = DANGER if a["nivel"] == "CRÍTICO" else WARNING
        para = Paragraph(
            f'<font color="#{color.hexval()[2:]}"><b>[{a["nivel"]}]</b></font> {a["msg"]}',
            _styles()["BodyText"],
        )
        out.append(para)
    return out


# ---------------------------------------------------------------------------
# Build PDF
# ---------------------------------------------------------------------------

def build_pdf(report: CollectReport, output_path: str | Path,
              cliente: str = "Cliente") -> Path:
    output_path = Path(output_path)
    doc = SimpleDocTemplate(
        str(output_path), pagesize=A4,
        leftMargin=1.6 * cm, rightMargin=1.6 * cm,
        topMargin=1.5 * cm, bottomMargin=1.5 * cm,
        title="Relatório de Volumetria",
    )
    st = _styles()
    story: list = []

    # ---------- capa / resumo executivo ----------
    story.append(Paragraph("Relatório Executivo de Volumetria", st["ExecTitle"]))
    story.append(Paragraph(
        f"<b>Cliente:</b> {cliente} &nbsp;&nbsp; "
        f"<b>Servidor:</b> {report.server.hostname or '—'} &nbsp;&nbsp; "
        f"<b>Gerado em:</b> {datetime.now().strftime('%d/%m/%Y %H:%M')}",
        st["BodyText"],
    ))
    story.append(Spacer(1, 8))
    story.append(_kpi_table(report))
    story.append(Spacer(1, 12))

    # Resumo executivo (texto)
    base_total = sum((i.db_size_gb or 0) for i in report.instances)
    growth_total = crescimento_total_mensal(report)
    crit_fs = [fs for fs in report.filesystems if fs.use_pct >= 90]
    resumo = (
        f"O ambiente analisado conta com <b>{len(report.instances)} instâncias</b> "
        f"de banco de dados totalizando <b>{_fmt_gb(base_total)}</b> de dados, "
        f"distribuídos em <b>{len(report.filesystems)} filesystems</b>. "
        f"O crescimento mensal estimado é de <b>{_fmt_gb(growth_total)}</b>, "
        f"considerando histórico de bases e geração de archive logs. "
    )
    if crit_fs:
        resumo += (f"Foram identificadas <b>{len(crit_fs)} partições críticas</b> "
                   f"(uso ≥ 90%), com risco imediato de indisponibilidade. ")
    else:
        resumo += "Nenhuma partição se encontra em estado crítico no momento. "
    resumo += ("Este relatório apresenta a situação atual, projeções para 12, 24 e 36 "
               "meses, riscos identificados e recomendações técnicas.")
    story.append(Paragraph(resumo, st["BodyJustify"]))
    story.append(Spacer(1, 10))

    # ---------- servidor ----------
    story.append(Paragraph("1. Infraestrutura — Servidor", st["H1Custom"]))
    story.append(_server_block(report, st))
    story.append(Spacer(1, 10))

    # ---------- filesystems ----------
    story.append(Paragraph("2. Filesystems", st["H1Custom"]))
    fs_chart = _chart_filesystem(report)
    if fs_chart:
        story.append(fs_chart)
    story.append(Spacer(1, 6))
    story.append(_filesystem_table(report))
    story.append(PageBreak())

    # ---------- ASM ----------
    if report.asm:
        story.append(Paragraph("3. Storage ASM", st["H1Custom"]))
        rows = [["Disk Group", "Total", "Livre", "Usado", "% Uso", "Redundância"]]
        for a in report.asm:
            rows.append([a.name, _fmt_gb(a.total_gb), _fmt_gb(a.usable_free_gb),
                         _fmt_gb(a.used_gb), f"{a.pct_used:.0f}%", a.redundancy])
        t = Table(rows, repeatRows=1,
                  colWidths=[3 * cm, 3 * cm, 3 * cm, 3 * cm, 2 * cm, 3 * cm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), PRIMARY),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
        ]))
        story.append(t)
        story.append(Spacer(1, 10))

    # ---------- instâncias ----------
    story.append(Paragraph("4. Instâncias e Projeções", st["H1Custom"]))
    story.append(_instances_table(report))
    story.append(Spacer(1, 10))
    g_chart = _chart_growth(report)
    if g_chart:
        story.append(g_chart)
    story.append(PageBreak())

    # ---------- projeção agregada ----------
    story.append(Paragraph("5. Projeção de Capacidade", st["H1Custom"]))
    p_chart = _chart_projection(report)
    if p_chart:
        story.append(p_chart)
    story.append(Spacer(1, 6))
    rows = [["Horizonte", "Tamanho projetado total", "Crescimento acumulado"]]
    base = base_total
    for m in (12, 24, 36):
        proj = base + growth_total * m
        rows.append([f"{m} meses", _fmt_gb(proj), _fmt_gb(proj - base)])
    t = Table(rows, colWidths=[4 * cm, 6 * cm, 6 * cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), PRIMARY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_BG]),
    ]))
    story.append(t)
    story.append(Spacer(1, 12))

    # ---------- backups ----------
    story.append(Paragraph("6. Backups", st["H1Custom"]))
    story.append(_backups_table(report))
    story.append(Spacer(1, 12))

    # ---------- riscos / alertas ----------
    story.append(Paragraph("7. Riscos e Alertas", st["H1Custom"]))
    for item in _alertas_block(report):
        story.append(item)
    story.append(Spacer(1, 12))

    # ---------- recomendações ----------
    story.append(Paragraph("8. Recomendações Técnicas", st["H1Custom"]))
    recos = []
    if crit_fs:
        mounts = ", ".join(f.mount for f in crit_fs)
        recos.append(f"Expandir imediatamente os filesystems críticos ({mounts}) "
                     f"para evitar indisponibilidade.")
    if growth_total > 0:
        recos.append(f"Provisionar pelo menos <b>{_fmt_gb(growth_total * 12)}</b> "
                     f"adicionais nos próximos 12 meses para acomodar o crescimento "
                     f"estimado da base e archives.")
    backed = {b.instancia for b in report.backups if b.instancia}
    sem_backup = [i for i in report.instances if i.aberto and i.nome.lower() not in backed]
    if sem_backup:
        recos.append(f"Validar política de backup para <b>{len(sem_backup)} instância(s)</b> "
                     f"sem backup identificado nesta coleta.")
    recos.append("Revisar políticas de retenção de archive logs — instâncias com alta "
                 "geração diária podem se beneficiar de compressão e expurgo automatizado.")
    recos.append("Monitorar mensalmente a evolução das bases para reavaliar projeções e "
                 "antecipar movimentos de capacidade.")
    for r in recos:
        story.append(Paragraph("• " + r, st["BodyJustify"]))
        story.append(Spacer(1, 3))

    # rodapé executivo
    story.append(Spacer(1, 18))
    story.append(Paragraph(
        "<i>Documento gerado automaticamente pela ferramenta de Análise de Volumetria. "
        "Os números refletem os dados da coleta fornecida e devem ser validados pelo "
        "DBA responsável antes de qualquer ação de capacidade.</i>",
        st["Small"],
    ))

    doc.build(story)
    return output_path
