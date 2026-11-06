# TrendsIA-P0: diff-analyzer

CLI que envia um diff (arquivo `.diff` ou repositório git) para um LLM compatível com a API da OpenAI e imprime um resumo da mudança, os arquivos alterados, possíveis problemas e sugestões.

## Quickstart

```bash
uv sync --frozen        # instala as versões exatas de uv.lock
cp .env.example .env    # preencha LLM_MODEL e LLM_API_KEY
uv run --frozen diff-analyzer examples/exemplo.diff
```

## Requisitos

- [uv](https://docs.astral.sh/uv/getting-started/installation/) 0.11.17 ou superior (< 0.12).
- Um endpoint de LLM compatível com a API de Chat Completions da OpenAI.
- git, para analisar um repositório.
- [gitleaks](https://github.com/gitleaks/gitleaks#installing) 8.19 ou superior, para `scripts/validate.sh`.

## Acesso ao LLM

A configuração vem do `.env`. Por isso, rode a CLI a partir da pasta do projeto. Cada chave está descrita em `.env.example`.

Provedor com chave de API (padrão do `.env.example`):

```dotenv
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=<modelo disponível na sua conta>
LLM_AUTH=required
LLM_API_KEY=<sua chave>
```

Servidor sem credencial: use `LLM_AUTH=none`, deixe `LLM_API_KEY` vazio e ajuste `LLM_BASE_URL` e `LLM_MODEL` para o servidor.

- A chave fica somente no `.env`. Nunca a coloque em `.env.example`, no código ou em um commit.
- O tamanho do contexto depende do provedor ou servidor.

## Execução

```bash
uv run --frozen diff-analyzer examples/exemplo.diff         # arquivo .diff
uv run --frozen diff-analyzer .                             # mudanças não commitadas (git diff HEAD)
uv run --frozen diff-analyzer . --staged                    # só as mudanças preparadas
uv run --frozen diff-analyzer . --commit HEAD               # um commit
uv run --frozen diff-analyzer . --range HEAD~3..HEAD        # intervalo de commits
uv run --frozen diff-analyzer "$PWD" --commit HEAD          # outro repositório: troque "$PWD" pelo caminho dele
```

O prompt padrão está em `src/diff_analyzer/prompts/default.md`; edite-o para mudar todas as execuções. Para uma execução só:

```bash
uv run --frozen diff-analyzer . --commit HEAD -p "Liste os riscos de segurança."
cp src/diff_analyzer/prompts/default.md meu-prompt.md
uv run --frozen diff-analyzer . --commit HEAD --prompt-file meu-prompt.md
```

A CLI não grava arquivos. Para salvar a análise, use:

```bash
uv run --frozen diff-analyzer . --commit HEAD > analise.md
```

Veja todas as opções com `uv run --frozen diff-analyzer --help`.

## Exemplo de entrada e saída

Entrada (`examples/exemplo.diff`):

```diff
diff --git a/app/sales.py b/app/sales.py
index 3b18e51..9c2d4f7 100644
--- a/app/sales.py
+++ b/app/sales.py
@@ -1,6 +1,13 @@
 """Simple statistics over the day's sales."""
 
 
 def total(sales):
     return sum(sale["amount"] for sale in sales)
 
+
+def average(sales):
+    return total(sales) / len(sales)
+
+
+def apply_discount(amount, percent):
+    return amount - amount * percent
```

Saída real, gerada com o modelo `qwen2.5-coder:7b` em um servidor local:

```text
1. **Resumo**: A mudança adiciona duas novas funções ao arquivo `sales.py`: `average` para calcular a média dos valores de vendas e `apply_discount` para aplicar um desconto a um valor.

2. **Arquivos alterados**:
   - `app/sales.py`: Este arquivo foi modificado para incluir as duas novas funções.

3. **Possíveis problemas**: 
   - Nenhum problema identificado.

4. **Sugestões**:
   - Adicionar um tratamento de caso onde a lista de vendas está vazia no cálculo da média (`average`), para evitar uma divisão por zero.
   - Considerar adicionar um tipo de verificação para garantir que os valores de desconto sejam válidos (por exemplo, entre 0 e 1).
```

## Códigos de saída

| Código | Significado |
| ------ | ----------- |
| 0 | Sucesso |
| 2 | Argumento inválido |
| 3 | Entrada inválida ou vazia (arquivo inexistente, diff vazio, referência git inválida) |
| 4 | Configuração inválida no `.env` ou git ausente |
| 5 | Credencial recusada (HTTP 401/403) |
| 6 | LLM inacessível ou tempo esgotado |
| 7 | Erro do LLM: modelo inexistente, contexto insuficiente para o diff, 429 ou 5xx |

## Testes

```bash
uv run --frozen pytest
./scripts/validate.sh   # lockfile, lint, formatação, mypy, testes com cobertura > 90%, gitleaks
```

## Problema

Revisar uma mudança ou entender o que um commit fez exige ler o diff linha a linha. Em diffs com vários arquivos isso toma tempo, e é fácil não perceber problemas ou o propósito das mudanças.

## Quem se beneficia

Desenvolvedores que revisam código, próprio ou de outra pessoa, ou que precisam entender o que uma mudança fez.

## Por que um LLM

Linters e checadores de tipo acham violações de regras fixas, mas nenhum explica a mudança ou aponta erros. Um LLM resume o diff, aponta riscos, e o formato da resposta muda pelo prompt.

## Como funciona

1. Lê a configuração do `.env`.
2. Lê o diff do arquivo ou do repositório (via `git`).
3. Envia o prompt como mensagem de sistema e o diff, entre `<diff>` e `</diff>`.
4. Imprime a resposta.

## Limites do projeto

- Só analisa e não altera código.
- Um diff por execução, sem divisão de diffs grandes.
- Não guarda nada além dos prompts em markdown.

## Limitações conhecidas

- **O modelo erra e omite.** Modelos podem não entender o código e os motivos das mudanças, especialmente por não ter o contexto do projeto inteiro, mas apenas o diff.
- **Corte silencioso.** Alguns servidores cortam uma entrada maior que o contexto sem avisar. O sistema não detecta isso e a resposta pode ignorar parte do diff ou o prompt.
- **Validações ausentes:** o conteúdo não é validado como diff; uma resposta cortada ou vazia não gera aviso.
- **Injeção de prompt.** Texto dentro do diff ainda pode mudar a resposta do modelo.
