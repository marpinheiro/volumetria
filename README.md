# Análise Profissional de Volumetria de Banco de Dados

Ferramenta em **Python + Streamlit** que interpreta arquivos TXT de coleta de
ambiente Oracle/DB e gera uma análise executiva completa, com projeções de
crescimento, alertas de capacidade e relatório PDF pronto para o cliente.

## Recursos

- **Parser robusto** tolerante a variações de formato, ausência de cabeçalhos,
  encodings (UTF-8 / latin-1) e valores em MB / GB / TB.
- Identifica automaticamente:
  - Bloco SERVIDOR (hardware, memória, hostname, IP, SO)
  - Filesystems (df -h / df -BG) com classificação CRÍTICO / ATENÇÃO / OK
  - Storage ASM (quando presente)
  - **Múltiplas instâncias** (loop automático), tamanho físico/lógico
  - Histórico de crescimento mensal (ignora valores zero/negativos na média)
  - Geração de archive logs (média diária e mensal)
  - Datafiles e tablespaces
  - Backups com **correlação automática à instância**
- **Projeções** para 12, 24 e 36 meses
- **Alertas** automáticos (disco crítico, crescimento acelerado, falta de backup)
- **Relatório PDF executivo** com KPIs, gráficos e recomendações técnicas
- Dashboard interativo (Streamlit + Plotly)

## Instalação

```bash
python -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Uso

```bash
streamlit run app.py
```

Em seguida, na barra lateral:
1. Informe o **nome do cliente**
2. Faça **upload do TXT da coleta** (ou marque "Usar arquivo de exemplo" se
   colocar `teste.txt` na raiz)
3. Navegue pelas abas: **Dashboard**, **Filesystems**, **Instâncias**,
   **Projeção**, **Backups**, **Alertas**
4. Em **Exportar PDF**, gere e baixe o relatório executivo

## Estrutura

```
volumetria/
├── app.py                  # interface Streamlit
├── src/
│   ├── parser.py           # parser do TXT + agregações
│   └── report_pdf.py       # gerador de PDF (reportlab + matplotlib)
├── requirements.txt
└── README.md
```

## Geração de PDF via linha de comando

```python
from src.parser import parse_collect
from src.report_pdf import build_pdf

report = parse_collect("teste.txt")
build_pdf(report, "relatorio.pdf", cliente="Meu Cliente")
```

## Notas técnicas

- O parser usa expressões regulares flexíveis e detecção automática de encoding.
- A média de crescimento considera os **últimos 12 meses válidos** (descarta
  zero e negativos, conforme regra do enunciado).
- O crescimento total mensal é a soma de **crescimento das bases** + **archive
  logs** (média diária × 30).
- O cálculo de "meses até lotar" por filesystem distribui o crescimento total
  proporcionalmente. Para precisão por mount, é necessário mapear datafiles →
  filesystem (não disponível na coleta padrão).
