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
.author-badge {
  display:inline-block; background:#eef3fa; color:#1f3a68;
  padding:4px 12px; border-radius:20px; font-size:12px; font-weight:600;
  border:1px solid #d6e2f2; margin-top:6px;
}
.app-footer {
  text-align:center; color:#6b7a8f; font-size:12px;
  padding:18px 0 6px; margin-top:30px; border-top:1px solid #e6ecf3;
}
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
st.sidebar.markdown("---")
st.sidebar.caption("👨‍💻 Desenvolvido por **Marciano Silva**")

if not uploaded and not use_sample:
    st.title("Análise Profissional de Volumetria")
    st.markdown('<span class="author-badge">👨‍💻 Desenvolvido por Marciano Silva</span>',
                unsafe_allow_html=True)
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
st.markdown('<span class="author-badge">👨‍💻 Desenvolvido por Marciano Silva</span>',
            unsafe_allow_html=True)
st.caption(
    f"Cliente: **{cliente}** · Servidor: **{report.server.hostname or '—'}** · "
    f"Gerado em {datetime.now().strftime('%d/%m/%Y %H:%M')}"
)

# -------- KPIs --------
crit = sum(1 for fs in report.filesystems if fs.use_pct >= 90)
warn = sum(1 for fs in report.filesystems if 75 <= fs.use_pct < 90)
growth_base_total = sum(i.media_crescimento_mensal_gb for i in report.instances)
archive_mes_total = sum(i.media_archive_mensal_gb for i in report.instances)
growth_total = growth_base_total  # compat
base_total = sum((i.db_size_gb or 0) for i in report.instances)

c1, c2, c3, c4, c5 = st.columns(5)
kpi(c1, "Instâncias", str(len(report.instances)),
    f"{sum(1 for i in report.instances if i.aberto)} abertas")
kpi(c2, "Volume total", fmt_gb(base_total), "soma de bases físicas")
kpi(c3, "Cresc. base/mês", fmt_gb(growth_base_total),
    f"+ {fmt_gb(archive_mes_total)}/mês em archives")
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
        fig.update_layout(height=380, showlegend=False, margin=dict(l=10, r=10, t=50, b=10))
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
        fig.update_layout(height=max(280, 28 * len(df)), margin=dict(l=10, r=10, t=50, b=10))
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
        "Cresc base/mês (GB)": round(i.media_crescimento_mensal_gb, 1),
        "% mês": round(i.pct_crescimento_mensal, 2),
        "Meses considerados": i.qtd_meses_considerados,
        "Archive/dia (GB)": round(i.media_archive_diaria_gb, 2),
        "Archive/mês (GB)": round(i.media_archive_mensal_gb, 1),
        "Local archives": i.archives_location or "—",
        "Aberto": "✅" if i.aberto else "⚠️",
    } for i in report.instances])
    st.dataframe(df.sort_values("Tamanho (GB)", ascending=False),
                 width="stretch", hide_index=True)

    st.markdown("### Detalhe por instância")
    sel = st.selectbox("Instância", [i.nome for i in report.instances])
    inst = next(i for i in report.instances if i.nome == sel)

    ca, cb, cc, cd = st.columns(4)
    kpi(ca, "Tamanho atual", fmt_gb(inst.db_size_gb or 0), "datafiles")
    kpi(cb, "Cresc. médio/mês",
        fmt_gb(inst.media_crescimento_mensal_gb),
        f"{inst.pct_crescimento_mensal:.2f}% • {inst.qtd_meses_considerados} meses")
    kpi(cc, "Archives/dia (média)",
        fmt_gb(inst.media_archive_diaria_gb),
        f"{fmt_gb(inst.media_archive_mensal_gb)}/mês")
    loc = inst.archives_location or "—"
    kpi(cd, "Local archives",
        (loc[:22] + "…") if len(loc) > 22 else loc,
        "log_archive_dest")

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
            height=420, margin=dict(l=10, r=10, t=50, b=10),
            legend=dict(orientation="h", y=1.1),
        )
        st.plotly_chart(fig, width="stretch")
    else:
        st.info("Sem histórico de crescimento para esta instância.")

    if inst.datafiles and report.filesystems:
        mounts = [fs.mount for fs in report.filesystems]
        dist = inst.datafiles_por_mount(mounts)
        if dist:
            st.markdown("#### 📂 Distribuição dos datafiles por filesystem")
            total_dist = sum(dist.values())
            df_dist = pd.DataFrame([
                {"Filesystem": k, "Tamanho atual (GB)": round(v, 1),
                 "% da base": round(100*v/total_dist, 1) if total_dist else 0}
                for k, v in sorted(dist.items(), key=lambda x: -x[1])
            ])
            st.dataframe(df_dist, width="stretch", hide_index=True)

    if inst.datafiles:
        with st.expander(f"Datafiles ({len(inst.datafiles)})"):
            df_d = pd.DataFrame([{
                "FID": d.fid, "Tablespace": d.tablespace, "Arquivo": d.filename,
                "% Uso": d.pct, "MB Usado": d.mb_used, "MB Máx.": d.mb_max,
            } for d in inst.datafiles])
            st.dataframe(df_d, width="stretch", hide_index=True)

# === Projeção ===
with tab_proj:
    st.markdown("### 🔮 Lógica de projeção")
    st.markdown("""
- **Crescimento da base** = média dos meses do histórico **ignorando zeros e negativos**
  (apenas meses com crescimento real entram no cálculo, dividido pela quantidade
  de meses válidos).
- **Archives** são gerados, copiados pelo backup e **apagados** — não somam ao
  tamanho da base no longo prazo, mas pressionam o filesystem de archives.
- **Backups** crescem proporcionalmente: tamanho da base + archives gerados no mês.
- **Filesystems** projetados individualmente conforme onde estão os datafiles
  de cada instância (mapeamento por caminho do arquivo).
    """)

    base_total = sum((i.db_size_gb or 0) for i in report.instances)
    growth_base = sum(i.media_crescimento_mensal_gb for i in report.instances)
    archive_mes = sum(i.media_archive_mensal_gb for i in report.instances)

    cp1, cp2, cp3 = st.columns(3)
    kpi(cp1, "Base total atual", fmt_gb(base_total), "soma datafiles")
    kpi(cp2, "Cresc. base/mês", fmt_gb(growth_base), "média meses válidos")
    kpi(cp3, "Archives/mês", fmt_gb(archive_mes), "geração total estimada")

    st.markdown("### Projeção total da BASE (12 / 24 / 36 meses)")
    months = list(range(0, 37))
    series = [base_total + growth_base * m for m in months]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=months, y=series, fill="tozeroy",
                             line=dict(color="#1f3a68", width=2),
                             fillcolor="rgba(46,125,209,0.25)", name="Base"))
    y_max = max(series) * 1.18 if series else 1
    for marker in (12, 24, 36):
        fig.add_vline(x=marker, line_dash="dash", line_color="grey")
        fig.add_annotation(
            x=marker, y=series[marker],
            text=f"<b>{marker}m</b><br>{fmt_gb(series[marker])}",
            showarrow=True, arrowhead=2, arrowcolor="grey",
            ax=0, ay=-40, yanchor="bottom",
            bgcolor="rgba(255,255,255,0.9)", bordercolor="#1f3a68", borderwidth=1,
            font=dict(size=11, color="#1f3a68"),
        )
    fig.update_layout(height=460, xaxis_title="Meses", yaxis_title="Tamanho da base (GB)",
                      margin=dict(l=10, r=20, t=70, b=40),
                      yaxis=dict(range=[0, y_max]))
    st.plotly_chart(fig, width="stretch")

    st.markdown("### 📂 Projeção de crescimento por destino físico (filesystem / ASM)")
    if report.instances:
        mounts = [fs.mount for fs in report.filesystems]
        # capacidade conhecida por destino
        cap_total: dict[str, float] = {fs.mount: fs.size_gb for fs in report.filesystems}
        cap_used: dict[str, float] = {fs.mount: fs.used_gb for fs in report.filesystems}
        cap_free: dict[str, float] = {fs.mount: fs.free_gb for fs in report.filesystems}
        for a in report.asm:
            key = f"ASM +{a.name}" if not a.name.startswith("+") else f"ASM {a.name}"
            cap_total[key] = a.total_gb
            cap_used[key] = a.used_gb
            cap_free[key] = a.usable_free_gb

        growth_por_dest: dict[str, float] = {}
        atual_por_dest: dict[str, float] = {}
        detalhes = []
        for i in report.instances:
            dist = i.datafiles_por_mount(mounts)
            total_dist = sum(dist.values())
            if total_dist <= 0:
                continue
            for dest, gb in dist.items():
                share = gb / total_dist
                atual_por_dest[dest] = atual_por_dest.get(dest, 0.0) + gb
                growth_por_dest[dest] = growth_por_dest.get(dest, 0.0) + \
                    i.media_crescimento_mensal_gb * share
                detalhes.append({
                    "Instância": i.nome,
                    "Tamanho instância (GB)": round(i.db_size_gb or 0, 1),
                    "Destino": dest,
                    "Datafiles aqui (GB)": round(gb, 1),
                    "% nesse destino": round(100*share, 1),
                    "Cresc. atribuído (GB/mês)":
                        round(i.media_crescimento_mensal_gb * share, 2),
                })

        if detalhes:
            st.markdown("#### Distribuição instância × destino")
            st.dataframe(pd.DataFrame(detalhes).sort_values(
                ["Instância", "Datafiles aqui (GB)"], ascending=[True, False]),
                width="stretch", hide_index=True)

        rows = []
        all_dest = sorted(set(list(cap_total.keys()) + list(growth_por_dest.keys())))
        for dest in all_dest:
            g = growth_por_dest.get(dest, 0.0)
            atual_dat = atual_por_dest.get(dest, 0.0)
            tam = cap_total.get(dest)
            usado = cap_used.get(dest)
            livre = cap_free.get(dest)
            meses_lotar = (livre / g) if (livre and g > 0) else None
            rows.append({
                "Destino": dest,
                "Capacidade (GB)": round(tam, 1) if tam else "—",
                "Usado (GB)": round(usado, 1) if usado is not None else "—",
                "Livre (GB)": round(livre, 1) if livre is not None else "—",
                "Datafiles aqui (GB)": round(atual_dat, 1),
                "Cresc/mês (GB)": round(g, 2),
                "Em 12m (GB)":
                    round((usado or 0) + g*12, 1) if usado is not None else "—",
                "Em 24m (GB)":
                    round((usado or 0) + g*24, 1) if usado is not None else "—",
                "Em 36m (GB)":
                    round((usado or 0) + g*36, 1) if usado is not None else "—",
                "Meses até lotar":
                    f"{meses_lotar:.1f}" if meses_lotar else "—",
            })
        df_fs = pd.DataFrame(rows)
        st.markdown("#### Consolidado por destino físico")
        st.dataframe(df_fs, width="stretch", hide_index=True)

        # gráfico (apenas destinos com capacidade conhecida e cresc > 0)
        chart_rows = [r for r in rows
                      if isinstance(r["Usado (GB)"], (int, float))
                      and r["Cresc/mês (GB)"] > 0]
        if chart_rows:
            fig = go.Figure()
            for r in chart_rows:
                xs = list(range(0, 37))
                ys = [r["Usado (GB)"] + r["Cresc/mês (GB)"]*x for x in xs]
                fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines",
                                         name=r["Destino"]))
                if isinstance(r["Capacidade (GB)"], (int, float)):
                    fig.add_hline(y=r["Capacidade (GB)"], line_dash="dot",
                                  line_color="rgba(192,57,43,0.35)")
            fig.update_layout(
                height=460, xaxis_title="Meses", yaxis_title="Uso projetado (GB)",
                margin=dict(l=10, r=20, t=50, b=60),
                legend=dict(orientation="h", y=-0.18),
            )
            st.plotly_chart(fig, width="stretch")
        st.caption("Linhas pontilhadas = capacidade total de cada destino.")
    else:
        st.info("Sem dados de filesystems ou instâncias para projeção detalhada.")

    st.markdown("### 📦 Geração de archives por instância")
    df_arch = pd.DataFrame([{
        "Instância": i.nome,
        "Archive/dia (GB)": round(i.media_archive_diaria_gb, 2),
        "Archive/mês (GB)": round(i.media_archive_mensal_gb, 1),
        "Local (destination)": i.archives_location or "—",
    } for i in report.instances if i.media_archive_diaria_gb > 0])
    if df_arch.empty:
        st.info("Nenhuma instância com geração de archive identificada.")
    else:
        st.dataframe(df_arch, width="stretch", hide_index=True)
        st.caption("ℹ️ Archives são copiados pelo backup e apagados — "
                   "não somam ao tamanho da base, mas dimensionam o "
                   "filesystem de archive e o repositório de backup.")

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

# -------- footer --------
st.markdown(
    '<div class="app-footer">📊 Análise Profissional de Volumetria · '
    'Desenvolvido por <b>Marciano Silva</b></div>',
    unsafe_allow_html=True,
)
