"""
App Streamlit — Análise Profissional de Volumetria.
Execução: streamlit run app.py
"""
from __future__ import annotations

import io
import tempfile
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from src.parser import (
    CollectReport, alertas, crescimento_total_mensal,
    filesystem_meses_ate_lotar, parse_collect,
)
from src.report_pdf import build_pdf

st.set_page_config(
    page_title="Análise de Volumetria — DBA Sênior",
    page_icon="📊",
    layout="wide",
)

# -------- estilo --------
st.markdown("""
<style>
.block-container {padding-top: 1.4rem;}
.metric-card {
  background: linear-gradient(135deg,#1f3a68 0%,#2e7dd1 100%);
  color: white; border-radius: 14px; padding: 18px 20px;
  box-shadow: 0 4px 14px rgba(0,0,0,.08);
}
.metric-card h3 {font-size: 13px; opacity: .85; margin: 0; font-weight: 500;}
.metric-card .value {font-size: 28px; font-weight: 700; margin-top: 6px;}
.metric-card .sub {font-size: 11px; opacity: .8; margin-top: 4px;}
.alert-crit {background:#fdecea; border-left:4px solid #c0392b; padding:10px 14px; border-radius:6px; margin-bottom:6px;}
.alert-warn {background:#fdf3e3; border-left:4px solid #d68910; padding:10px 14px; border-radius:6px; margin-bottom:6px;}
.section-title {color:#1f3a68; font-weight:700; margin-top:6px;}
</style>
""", unsafe_allow_html=True)


# -------- helpers de formatação --------
def fmt_gb(v: float | None) -> str:
    if v is None or v == 0:
        return "—"
    if v >= 1024:
        return f"{v/1024:,.2f} TB"
    return f"{v:,.1f} GB"


def kpi(col, title, value, sub=""):
    col.markdown(
        f'<div class="metric-card"><h3>{title}</h3>'
        f'<div class="value">{value}</div>'
        f'<div class="sub">{sub}</div></div>',
        unsafe_allow_html=True,
    )


# -------- sidebar / upload --------
st.sidebar.title("📊 Volumetria DBA")
st.sidebar.caption("Análise executiva de coleta de ambiente")
cliente = st.sidebar.text_input("Nome do cliente", value="Cliente")
uploaded = st.sidebar.file_uploader("Arquivo TXT da coleta", type=["txt"])
use_sample = st.sidebar.checkbox("Usar arquivo de exemplo (teste.txt)", value=False)

if not uploaded and not use_sample:
    st.title("Análise Profissional de Volumetria")
    st.info("⬅️ Envie o arquivo TXT da coleta na barra lateral para iniciar a análise.")
    st.markdown("""
**A ferramenta interpreta automaticamente:**
- Bloco SERVIDOR (hardware, memória, SO, hostname, IP)
- Filesystems (df -h / df -BG) com identificação de partições críticas
- Storage ASM (quando presente)
- Múltiplas instâncias de banco com loop automático
- Histórico de crescimento mensal (ignora valores zero/negativos)
- Geração de archive logs (média diária e projeção mensal)
- Datafiles e tablespaces
- Backups com correlação automática à instância

**Entrega:** dashboard executivo, projeções de 12/24/36 meses, alertas
e relatório PDF pronto para apresentação ao cliente.
    """)
    st.stop()

# carregar arquivo
if uploaded:
    tmp = Path(tempfile.mkstemp(suffix=".txt")[1])
    tmp.write_bytes(uploaded.read())
    source_path = tmp
else:
    source_path = Path("teste.txt")
    if not source_path.exists():
        st.error("Arquivo de exemplo teste.txt não encontrado no diretório do app.")
        st.stop()

with st.spinner("Interpretando coleta..."):
    report: CollectReport = parse_collect(source_path)

# -------- header --------
st.title("Análise Profissional de Volumetria")
st.caption(
    f"Cliente: **{cliente}** · Servidor: **{report.server.hostname or '—'}** · "
    f"Gerado em {datetime.now().strftime('%d/%m/%Y %H:%M')}"
)

# -------- KPIs --------
crit = sum(1 for fs in report.filesystems if fs.use_pct >= 90)
warn = sum(1 for fs in report.filesystems if 75 <= fs.use_pct < 90)
growth_total = crescimento_total_mensal(report)
base_total = sum((i.db_size_gb or 0) for i in report.instances)

c1, c2, c3, c4, c5 = st.columns(5)
kpi(c1, "Instâncias", str(len(report.instances)),
    f"{sum(1 for i in report.instances if i.aberto)} abertas")
kpi(c2, "Volume total", fmt_gb(base_total), "soma de bases físicas")
kpi(c3, "Crescimento/mês", fmt_gb(growth_total), "base + archives")
kpi(c4, "Filesystems", str(len(report.filesystems)),
    f"{crit} críticos · {warn} atenção")
kpi(c5, "Backups identificados", str(len(report.backups)),
    f"{len({b.instancia for b in report.backups if b.instancia})} instâncias cobertas")

st.divider()

# -------- tabs --------
tab_dash, tab_fs, tab_inst, tab_proj, tab_bkp, tab_alert, tab_export = st.tabs(
    ["📈 Dashboard", "💾 Filesystems", "🗄️ Instâncias", "🔮 Projeção",
     "🛟 Backups", "⚠️ Alertas", "📄 Exportar PDF"],
)

# === Dashboard ===
with tab_dash:
    st.markdown("### Servidor")
    s = report.server
    cs1, cs2, cs3 = st.columns(3)
    cs1.markdown(f"**Hostname:** {s.hostname or '—'}  \n**IP:** {s.ip or '—'}")
    cs2.markdown(f"**Fabricante:** {s.fabricante or '—'}  \n**Modelo:** {s.modelo or '—'}")
    cs3.markdown(f"**SO:** {s.so or '—'}  \n**Memória:** {s.memoria_label()}")

    st.markdown("### Top instâncias por crescimento mensal")
    df = pd.DataFrame([
        {"Instância": i.nome, "Crescimento/mês (GB)": i.media_crescimento_mensal_gb,
         "Tamanho atual (GB)": i.db_size_gb or 0}
        for i in report.instances if i.crescimento
    ]).sort_values("Crescimento/mês (GB)", ascending=False).head(10)
    if not df.empty:
        fig = px.bar(df, x="Instância", y="Crescimento/mês (GB)",
                     color="Crescimento/mês (GB)",
                     color_continuous_scale="Blues",
                     hover_data=["Tamanho atual (GB)"])
        fig.update_layout(height=380, showlegend=False, margin=dict(l=0, r=0, t=10, b=0))
        st.plotly_chart(fig, width="stretch")
    else:
        st.info("Nenhuma instância com histórico de crescimento.")

# === Filesystems ===
with tab_fs:
    if not report.filesystems:
        st.warning("Nenhum filesystem identificado.")
    else:
        df = pd.DataFrame([{
            "Mount": fs.mount, "Tipo": fs.fs_type,
            "Tamanho (GB)": round(fs.size_gb, 1),
            "Usado (GB)": round(fs.used_gb, 1),
            "Livre (GB)": round(fs.free_gb, 1),
            "% Uso": fs.use_pct, "Status": fs.status,
        } for fs in report.filesystems])

        def color_status(val):
            if val == "CRÍTICO": return "background-color:#fdecea;color:#c0392b;font-weight:700"
            if val == "ATENÇÃO": return "background-color:#fdf3e3;color:#d68910;font-weight:700"
            return "background-color:#e8f6ee;color:#1e8449;font-weight:700"

        st.dataframe(
            df.style.map(color_status, subset=["Status"]),
            width="stretch", hide_index=True,
        )

        fig = px.bar(df.sort_values("% Uso"), x="% Uso", y="Mount", orientation="h",
                     color="Status",
                     color_discrete_map={"CRÍTICO": "#c0392b", "ATENÇÃO": "#d68910",
                                          "OK": "#1e8449"},
                     text="% Uso")
        fig.update_layout(height=max(280, 28 * len(df)), margin=dict(l=0, r=0, t=10, b=0))
        st.plotly_chart(fig, width="stretch")

    if report.asm:
        st.markdown("### Storage ASM")
        df_asm = pd.DataFrame([{
            "Disk Group": a.name, "Total (GB)": a.total_gb,
            "Livre (GB)": a.usable_free_gb, "Usado (GB)": a.used_gb,
            "% Uso": a.pct_used, "Redundância": a.redundancy,
        } for a in report.asm])
        st.dataframe(df_asm, width="stretch", hide_index=True)

# === Instâncias ===
with tab_inst:
    df = pd.DataFrame([{
        "Instância": i.nome, "Tipo": i.tipo,
        "Tamanho (GB)": round(i.db_size_gb or 0, 1),
        "Usado (GB)": round(i.db_used_gb or 0, 1),
        "Livre (GB)": round(i.db_free_gb or 0, 1),
        "Cresc/mês (GB)": round(i.media_crescimento_mensal_gb, 1),
        "Archive/dia (GB)": round(i.media_archive_diaria_gb, 1),
        "Cresc total/mês (GB)": round(i.crescimento_total_mensal_gb, 1),
        "Aberto": "✅" if i.aberto else "⚠️",
    } for i in report.instances])
    st.dataframe(df.sort_values("Tamanho (GB)", ascending=False),
                 width="stretch", hide_index=True)

    st.markdown("### Detalhe de crescimento por instância")
    sel = st.selectbox("Instância", [i.nome for i in report.instances])
    inst = next(i for i in report.instances if i.nome == sel)
    if inst.crescimento:
        df_g = pd.DataFrame([{
            "Período": g.period, "Tamanho (GB)": round(g.total_gb, 1),
            "Crescimento mensal (GB)": round(g.growth_gb, 1),
        } for g in inst.crescimento])
        df_g["Crescimento acumulado (GB)"] = df_g["Crescimento mensal (GB)"].cumsum()
        st.dataframe(df_g, width="stretch", hide_index=True)
        fig = go.Figure()
        fig.add_trace(go.Bar(x=df_g["Período"], y=df_g["Crescimento mensal (GB)"],
                             name="Crescimento mensal", marker_color="#2e7dd1"))
        fig.add_trace(go.Scatter(x=df_g["Período"], y=df_g["Tamanho (GB)"],
                                 mode="lines+markers", name="Tamanho da base",
                                 yaxis="y2", line=dict(color="#1f3a68", width=2)))
        fig.update_layout(
            yaxis=dict(title="Crescimento mensal (GB)"),
            yaxis2=dict(title="Tamanho da base (GB)", overlaying="y", side="right"),
            height=420, margin=dict(l=0, r=0, t=10, b=0),
            legend=dict(orientation="h", y=1.1),
        )
        st.plotly_chart(fig, width="stretch")
    else:
        st.info("Sem histórico de crescimento para esta instância.")

    if inst.datafiles:
        with st.expander(f"Datafiles ({len(inst.datafiles)})"):
            df_d = pd.DataFrame([{
                "FID": d.fid, "Tablespace": d.tablespace, "Arquivo": d.filename,
                "% Uso": d.pct, "MB Usado": d.mb_used, "MB Máx.": d.mb_max,
            } for d in inst.datafiles])
            st.dataframe(df_d, width="stretch", hide_index=True)

# === Projeção ===
with tab_proj:
    st.markdown("### Projeção total de capacidade")
    months = list(range(0, 37))
    series = [base_total + growth_total * m for m in months]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=months, y=series, fill="tozeroy",
                             line=dict(color="#1f3a68", width=2),
                             fillcolor="rgba(46,125,209,0.25)", name="Projeção"))
    for marker in (12, 24, 36):
        fig.add_vline(x=marker, line_dash="dash", line_color="grey",
                      annotation_text=f"{marker}m: {fmt_gb(series[marker])}",
                      annotation_position="top")
    fig.update_layout(height=420, xaxis_title="Meses", yaxis_title="Tamanho total (GB)",
                      margin=dict(l=0, r=0, t=10, b=0))
    st.plotly_chart(fig, width="stretch")

    st.markdown("### Tempo estimado até lotar (por filesystem)")
    if report.filesystems:
        per_fs_growth = growth_total / max(1, len(report.filesystems))
        df_t = pd.DataFrame([{
            "Mount": fs.mount, "Livre (GB)": round(fs.free_gb, 1),
            "Cresc atribuído (GB/mês)": round(per_fs_growth, 1),
            "Meses até lotar": (
                f"{filesystem_meses_ate_lotar(fs, per_fs_growth):.1f}"
                if filesystem_meses_ate_lotar(fs, per_fs_growth) else "—"
            ),
        } for fs in report.filesystems])
        st.dataframe(df_t, width="stretch", hide_index=True)
        st.caption("⚠️ O crescimento total é distribuído proporcionalmente entre os "
                   "filesystems. Para precisão por mount, mapeie datafiles → mount.")

# === Backups ===
with tab_bkp:
    if not report.backups:
        st.warning("Nenhum backup identificado na coleta.")
    else:
        df = pd.DataFrame([{
            "Tipo": b.tipo, "Instância": b.instancia or "—",
            "Diretório": b.diretorio, "Tamanho (GB)": b.tamanho_gb,
            "Início": b.horario_inicio, "Duração": b.duracao,
            "Contexto": b.contexto,
        } for b in report.backups])
        st.dataframe(df, width="stretch", hide_index=True)

# === Alertas ===
with tab_alert:
    items = alertas(report)
    if not items:
        st.success("Nenhum alerta crítico identificado. ✅")
    else:
        crits = [a for a in items if a["nivel"] == "CRÍTICO"]
        warns = [a for a in items if a["nivel"] == "ATENÇÃO"]
        if crits:
            st.markdown("#### 🔴 Críticos")
            for a in crits:
                st.markdown(f'<div class="alert-crit"><b>{a["nivel"]}</b> — {a["msg"]}</div>',
                            unsafe_allow_html=True)
        if warns:
            st.markdown("#### 🟠 Atenção")
            for a in warns:
                st.markdown(f'<div class="alert-warn"><b>{a["nivel"]}</b> — {a["msg"]}</div>',
                            unsafe_allow_html=True)

# === Export PDF ===
with tab_export:
    st.markdown("### Relatório executivo PDF")
    st.write("O PDF inclui resumo executivo, situação atual, projeções, "
             "riscos e recomendações em linguagem clara para apresentação ao cliente.")
    if st.button("📄 Gerar PDF agora", type="primary"):
        with st.spinner("Gerando PDF..."):
            out = Path(tempfile.mkstemp(suffix=".pdf")[1])
            build_pdf(report, out, cliente=cliente)
            data = out.read_bytes()
        st.success(f"PDF gerado ({len(data)/1024:.0f} KB)")
        st.download_button(
            "⬇️ Baixar relatório",
            data=data,
            file_name=f"relatorio_volumetria_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf",
            mime="application/pdf",
        )
