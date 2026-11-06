# TrendsIA-P0: diff-analyzer

CLI que envia um diff (arquivo `.diff` ou repositório git) para um LLM via API REST compatível com OpenAI e imprime a análise no terminal.

> **Estado atual:** a CLI ainda não está implementada. Configuração, leitura do diff, prompt e cliente LLM já estão implementados e têm testes unitários.

## Requisitos

| Ferramenta | Versão | Uso |
| ---------- | ------ | --- |
| [uv](https://docs.astral.sh/uv/getting-started/installation/) | 0.11.17 ou superior (< 0.12) | Ambiente, dependências e execução |
| Python | 3.12 (fixado em `.python-version`) | Instalado automaticamente pelo uv se não existir |
| [gitleaks](https://github.com/gitleaks/gitleaks#installing) | 8.19 ou superior (testado com 8.30.1) | Apenas para `scripts/validate.sh` |
| git | qualquer | Opcional: necessário apenas para ler o diff de um repositório |

## Configuração do ambiente

```bash
uv sync --frozen        # instala as versões exatas de uv.lock em .venv/
cp .env.example .env    # configuração local; .env está no .gitignore
```

A configuração vem somente do arquivo `.env` no diretório atual; o que não estiver nele usa o padrão do código. Variáveis de ambiente do processo são ignoradas. Cada chave está descrita em `.env.example`. Nunca coloque uma credencial real em `.env.example` ou em qualquer arquivo versionado.

## Validação

```bash
./scripts/validate.sh
```

Executa, nesta ordem, e para na primeira falha:

1. `uv lock --check`: `uv.lock` corresponde ao `pyproject.toml`;
2. `ruff check`: lint;
3. `ruff format --check`: formatação;
4. `mypy`: checagem de tipos (modo estrito) em `src/` e `tests/`;
5. `pytest` com cobertura acima de 90% (`fail_under = 90.01` com 2 casas decimais: exatamente 90,00% falha);
6. `gitleaks`: procura segredos no histórico do git e nas alterações staged e não staged (arquivos não rastreados só são verificados depois de `git add`).

## Estrutura

```text
src/diff_analyzer/
  cli.py           # argumentos (argparse), códigos de saída, mensagens em stderr
  service.py       # orquestração: origem do diff -> validação -> prompt -> LLM
  diff_source.py   # leitura de arquivo ou repositório git + validação do diff
  prompt.py        # prompt padrão, --prompt-file ou --prompt; montagem das mensagens
  llm_client.py    # protocolo LLMClient + implementação compatível com OpenAI
  config.py        # configuração (.env > padrões)
  errors.py        # hierarquia de exceções de domínio
  prompts/
    default.md     # prompt padrão
tests/
  fakes.py         # FakeLLMClient, usado nos testes sem chamada real ao LLM
  unit/
  e2e/
  fixtures/
scripts/
  validate.sh
```
