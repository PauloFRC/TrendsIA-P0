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

## Acesso ao LLM

A CLI funciona com qualquer provedor ou servidor que ofereça a API de Chat Completions da OpenAI. O endpoint, o modelo e a credencial ficam no `.env`.

### Provedor com chave de API

O `.env.example` já vem configurado para a OpenAI; no `.env`, preencha `LLM_MODEL` e `LLM_API_KEY`:

```dotenv
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=<modelo disponível na sua conta>
LLM_AUTH=required
LLM_API_KEY=<sua chave>
LLM_DATA_EGRESS=external
```

`LLM_MODEL` não tem valor padrão: sem ele, a CLI termina com código 4 e a mensagem "Configuração inválida no .env: LLM_MODEL (informe o nome do modelo do provedor).". Para outro provedor compatível, use em `LLM_BASE_URL` e `LLM_MODEL` os valores da documentação dele.

- A chave fica somente no `.env`, que está no `.gitignore`. Nunca a coloque em `.env.example`, no código ou em um commit. A variável de ambiente `OPENAI_API_KEY` é ignorada.
- `LLM_DATA_EGRESS=external`: o diff sai desta máquina e é enviado ao provedor. Não analise código que não pode sair da sua rede.
- Esta configuração não foi executada com uma chave real neste projeto.

### Endpoint sem credencial

Para um servidor que não exige chave, por exemplo na sua máquina ou na sua rede, use `LLM_AUTH=none` e deixe `LLM_API_KEY` vazio. Se o diff não sai da máquina, use `LLM_DATA_EGRESS=local`. Ajuste `LLM_BASE_URL` e `LLM_MODEL` para o seu servidor.

### Contexto do modelo

`LLM_MAX_INPUT_CHARS` (padrão 60000) só limita o tamanho do diff em caracteres. Ele não configura nem garante que o diff caiba no contexto do modelo: 60000 caracteres equivalem a cerca de 15000 a 20000 tokens, sem contar o prompt e a resposta.

O tamanho do contexto é definido pelo provedor ou servidor, não pela CLI; consulte a documentação dele. Para diffs perto do limite padrão, use um modelo e um servidor com contexto de pelo menos 32K tokens. Quando a entrada não cabe no contexto:

- servidores que recusam o pedido, como a API da OpenAI, fazem a CLI terminar com código 7 e a mensagem "O servidor do LLM não oferece contexto suficiente para este diff com o modelo ...";
- alguns servidores cortam a entrada sem avisar: a resposta chega sem erro e a CLI termina com código 0, mas o modelo não recebeu o pedido inteiro. A CLI não consegue detectar esse caso, porque a API não informa o corte nem o tamanho do contexto.

Possíveis sinais de que a entrada foi cortada: a resposta não segue o formato do prompt (por exemplo, vem em inglês ou em JSON em vez das seções em português) ou fala só dos últimos arquivos do diff. Esses sinais não provam o corte: um modelo também pode ignorar as instruções de formato com a entrada inteira. Nos testes deste projeto, um pedido grande demais chegou encurtado ao modelo e o servidor pode ter descartado as instruções; um pedido menor seguiu o formato. Na dúvida, aumente o contexto no servidor ou analise um diff menor, por exemplo um único commit com `--commit` ou um intervalo menor com `--range`.

### Reprodutibilidade

Cada pedido envia `temperature=0` e `seed` (`LLM_TEMPERATURE` e `LLM_SEED`, padrão 42), e o modelo é o nome exato em `LLM_MODEL`; quando o provedor oferecer, use um nome com versão fixa. Mesmo assim, a mesma entrada não tem garantia de gerar a mesma resposta: o resultado pode mudar com a versão do modelo ou do servidor, o hardware e pedidos simultâneos.

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
examples/
  exemplo.diff     # diff de exemplo para testar a CLI
```
