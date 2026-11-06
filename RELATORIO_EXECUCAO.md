# Relatório de execução: diff-analyzer

Estado em 2026-11-06

## Evolução por nível

### Básico (`p0-basico`)

CLI que envia um diff (arquivo ou repositório git) a um endpoint compatível com a API da OpenAI e imprime a análise. Cada commit foi revalidado com `scripts/validate.sh`.

| Commit | Tarefa | `validate.sh` |
| --- | --- | --- |
| `e9ef152` | Scaffold: `uv`, versões exatas, `.env.example`, `validate.sh`, prompt padrão | Falha (mypy) |
| `a226bf7` | Hierarquia de erros com códigos de saída 3 a 7 | 8 testes, 100% |
| `79227c4` | Configuração só pelo `.env`, sem exibir valores em erros | 40 testes, 99,09% |
| `74eebc5` | Diff de arquivo ou git (`HEAD`, `--staged`, `--commit`, `--range`) | 96 testes, 99,11% |
| `aacc991` | Prompt padrão, `-p`, `--prompt-file`; diff entre `<diff>` e `</diff>` | 117 testes, 98,91% |
| `266b27b` | Cliente LLM com SDK `openai`, `FakeLLMClient`, erros HTTP | 139 testes, 99,10% |
| `174fd55` | Orquestração; diff vazio barrado antes do LLM | Falha (`ruff format`) |
| `cda72cb` | CLI com argparse; stdin removido | 155 testes, 99,26% |
| `15a53ca` | Erro claro quando o servidor rejeita o diff por contexto | 157 testes, 99,28% |
| `46106e5` | Neutralidade de provedor; exemplo e execuções reais | 160 testes, 99,28% |

### Intermediário (`p0-intermediario`)

Não iniciado. Planejado: validação de formato e tamanho do diff, política de credencial, timeouts e retries, `--verbose`, Ctrl+C e validação em ambiente limpo.

### Avançado (`p0-avancado`)

Não iniciado. Planejado: bloqueio de segredos no diff, proteção contra injeção de prompt, suítes E2E com stub e com LLM real.

## Decisões principais

| Decisão | Alternativa considerada | Justificativa |
| --- | --- | --- |
| Entrada: arquivo `.diff` ou repositório git, num único argumento `SOURCE` | stdin (implementado e depois removido); GitPython | Cobre o diff recebido pronto e as mudanças locais. O `git` roda via `subprocess`, sem dependência extra, e só é exigido para repositórios. |
| Prompt: padrão em markdown no pacote, `--prompt-file` ou `-p` inline, enviado como mensagem de sistema | Prompt com placeholder `{diff}` | O prompt muda sem editar código. O diff vai separado, entre `<diff>` e `</diff>`, e funciona com qualquer prompt. |
| Acesso ao LLM: SDK `openai` com `base_url` configurável, neutro de provedor (URL padrão da OpenAI) e sem detecção de contexto (limite `LLM_MAX_INPUT_CHARS` previsto para o Intermediário) | `litellm`; `httpx` direto; servidor local como padrão; tokenizer | Um só código para qualquer endpoint compatível, com exceções tipadas, e a maioria usa chave de API. O protocolo não informa o contexto, e tokenizer depende do modelo. |
| Configuração só pelo `.env` | Padrão do `pydantic-settings` (ambiente mais `.env`) | Mesma pasta, mesma configuração. Nada do shell, como `OPENAI_API_KEY`, entra sem estar no `.env`. |
| Validação: `validate.sh` antes de cada commit (lockfile, ruff, mypy, pytest com cobertura acima de 90%, gitleaks) | pre-commit ou serviço de CI | Um só comando local, sem serviço externo. |

## Testes e resultados

Execuções sobre `46106e5`. LLM real: servidor local compatível com a OpenAI (Ollama 0.31.1), `qwen2.5-coder:7b`, `temperature=0`, `seed=42`. Os casos 401 e de contexto usaram um servidor stub local.

| Teste | Entrada ou situação | Resultado esperado | Resultado obtido |
| --- | --- | --- | --- |
| Validação completa | `./scripts/validate.sh` | Todos os gates passam | 160 testes, cobertura 99,28%, gitleaks sem vazamentos |
| Entrada válida | `examples/exemplo.diff`; `. --commit 15a53ca` | Análise em português, exit 0 | Exit 0 nos dois, quatro seções em português |
| Reprodutibilidade | Mesmo arquivo duas vezes | Saídas iguais (não garantido) | Idênticas e iguais ao exemplo do README |
| Sem chamada real | Diff vazio com `FakeLLMClient` | Erro, fake nunca chamado | Passou, 0 chamadas |
| Argumentos inválidos | Sem `SOURCE`; `--staged --commit HEAD` | Exit 2 | Exit 2 |
| Entrada vazia ou inválida | Arquivo só com espaços, arquivo inexistente, referência git inválida | Exit 3 com orientação | Exit 3, ex.: `O diff está vazio; não há nada para analisar.` |
| Configuração inválida | `LLM_MODEL=`, `LLM_AUTH=talvez`, git fora do `PATH` | Exit 4, sem exibir valores | Exit 4, ex.: `LLM_AUTH (use required ou none)` |
| Credencial inválida | Stub responde 401 | Exit 5, chave não aparece | Exit 5, chave ausente da saída |
| Falha de comunicação | Porta fechada | Exit 6 | Exit 6: `Não foi possível falar com o LLM ...` |
| Erro do LLM | Modelo inexistente (404) e contexto excedido (400) | Exit 7 | Exit 7, mensagem de contexto |
| Credencial ausente | `LLM_AUTH=required`, chave vazia | Exit 4 | **Falhou:** traceback, exit 1 |
| Entrada fora do formato | Texto comum | Exit 3 | **Falhou:** exit 0, texto analisado |

## Falhas e limitações

### Corte silencioso do diff

- **O que aconteceu:** com 4096 tokens de contexto no servidor local, um diff de 25367 caracteres voltou com HTTP 200 e só 2050 tokens lidos. A resposta cobriu somente parte dos arquivos.
- **Como foi identificado:** comparando `usage.prompt_tokens` com o tamanho enviado.
- **Como foi tratado:** contexto do servidor aumentado para 32768, e a resposta voltou ao normal.
- **Por que permanece:** o protocolo não informa o corte.

## Uso de IA no desenvolvimento

Harness Oh-My-Pi (OMP). Claude Opus 5.5 foi o agente principal. GPT 6.1 Sol revisou as entregas durante as sessões, como advisor do OMP, e também em sessões separadas, como revisor independente.

| Ferramenta | Tarefa delegada | Resultado aproveitado | Correções ou alterações | Verificação |
| --- | --- | --- | --- | --- |
| Claude Opus 5.5 | Recomendar a stack | SDK `openai`, ruff, gitleaks | `respx` e `typer` descartados | Pesquisa e verificação do desenvolvedor |
| Claude Opus 5.5 | Detalhar a especificação e dividi-la em tarefas por nível | Regras de entrada, credenciais, códigos de saída e testes | stdin retirado do escopo, simplificação do código, mudança de arquitetura | Revisão do desenvolvedor |
| Claude Opus 5.5 | Implementar com TDD | Código de `src/`, testes | `cli.py` simplificado, código com provedor neutro | Testes falhando antes da mudança |
| GPT 6.1 Sol (advisor do OMP) | Revisar cada entrega durante a sessão | Testes faltando ou mal formulados (ex.: comparar o texto exato da mensagem de erro não provava que segredos ficam fora dela), validações faltando (`${VAR}` do `.env` lido do ambiente), melhorias na execução |  | Testes de regressão falhando antes da correção |
| GPT 6.1 Sol (revisor independente) | Buscar melhorias e ideias de testes mais robustos nas mudanças não commitadas | Casos de testes novos, diff preservado byte a byte, `.env` malformado, asserts frágeis |  | Testes novos bem sucedidos antes da implementação de features |

## Reflexão final

- **Principal aprendizado:**
Embora o projeto seja uma ideia bem simples, para atingir um nível de qualidade alto, considerando segurança dos dados, reproducibilidade e experiência de usuário, uma série de boas práticas tiveram que surgir. Nesse caso, mesmo tendo experiência com UV, aprendi mais sobre o uso do freeze para garantir versões fixas, sobre constantemente atualizar o AGENTS.md para dar contexto para as LLMs considerando o padrões que queria para o projeto. Também verifiquei a importância de revisar manualmente o código linha a linha para entender o que estava sendo construído, visto que muitas vezes a IA seguia em direções diferentes ao que eu estava buscando, e também vi como é mais complicado do que eu esperava garantir que informações sensíveis não apareçam.

- **Competência mais desenvolvida:** 
Segurança de informações sensíveis, visto que primeiramente acreditava que usando somente .env as informações estariam seguras, mas pesquisando mais a fundo vi como hávia outras práticas importantes de segurança, como evitar mensagens de erros automáticas sem validação e uso de Gitleaks. 

- **Aspecto que ainda precisa ser aprimorado:** 
Um dos principais problemas é em relação a limite de contexto do servidor e possibilidade de prompt-injection. Atualmente não há mensagem de erro consistente caso o contexto do diff seja maior que do servidor, nem técnicas mais robustar para ajudar a evitar prompt-injection, embora provavelmente não seja possível resolve-lo 100%.