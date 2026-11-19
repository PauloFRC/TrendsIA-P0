# Relatório de execução: diff-analyzer

Estado em 2026-11-19

## Evolução por nível

### Básico (`p0-basico`)

CLI que envia um diff (arquivo ou repositório git) a uma LLM e imprime a análise. 

| Commit | Tarefa | `validate.sh` |
| --- | --- | --- |
| `e9ef152` | Falha (mypy) |
| `a226bf7` | 8 testes, 100% |
| `79227c4` | 40 testes, 99,09% |
| `74eebc5` | 96 testes, 99,11% |
| `aacc991` | 117 testes, 98,91% |
| `266b27b` | 139 testes, 99,10% |
| `174fd55` | Falha (`ruff format`) |
| `cda72cb` | 155 testes, 99,26% |
| `15a53ca` | 157 testes, 99,28% |
| `46106e5` | 160 testes, 99,28% |
| `4af3d63` | 196 testes, 99,39% |
| `06d5e2c` | 197 testes, 99,23% |

### Intermediário (`p0-intermediario`)

Não iniciado. Planejado: validação de formato e tamanho do diff, política de credencial, timeouts e retries, `--verbose` e Ctrl+C.

### Avançado (`p0-avancado`)

Não iniciado. Planejado: bloqueio de segredos no diff, proteção contra injeção de prompt, suítes E2E.

## Decisões principais

| Decisão | Alternativa considerada | Justificativa |
| --- | --- | --- |
| Entrada: arquivo `.diff` ou repositório git, num único argumento `SOURCE` | stdin, GitPython | Cobre o diff recebido pronto e as mudanças locais. O `git` roda via `subprocess`, sem dependência extra. |
| Prompt: padrão em markdown no pacote, `--prompt-file` ou `-p` inline | Prompt com placeholder `{diff}` | O prompt muda sem editar código. O diff vai separado, entre `<diff>` e `</diff>`, e funciona com qualquer prompt. |
| Acesso ao LLM: SDK `openai` com `base_url` configurável e sem detecção de contexto | `litellm`, `httpx` direto, servidor local como padrão | Um só código para qualquer endpoint compatível. |
| Configuração só pelo `.env` | ambiente mais `.env` | Mesma pasta, mesma configuração. |
| Validação: `validate.sh` antes de cada commit (lockfile, ruff, mypy, pytest, gitleaks); cobertura acima de 90% | pre-commit ou serviço de CI | Um só comando local, sem serviço externo. |
| Erro no `.env` aponta o número da linha, não o conteúdo |  | Uma chave de API colada no lugar da linha inteira vira um "nome de chave". |
| Endpoint nas mensagens de erro só como esquema, host e porta | | Gateways podem levar token no caminho da URL, e as mensagens vão para logs e capturas de tela. |
| Depois de criar o cliente do SDK, zerar organização, projeto e cabeçalhos lidos do shell | Opções públicas do SDK (`default_headers`, `extra_headers`) | Um `Authorization` em `OPENAI_CUSTOM_HEADERS` substituía a chave do `.env`. As opções públicas ainda deixavam passar outros cabeçalhos, como `api-key`. O campo zerado é privado, mas a versão do `openai` é fixa e um teste compara a requisição inteira com a de um ambiente limpo. |
| Chave ausente ou em branco barrada no cliente LLM, antes de criar o cliente do SDK, com exit 4 |  | Só o cliente LLM lê o valor da chave. O erro do SDK não cobre a chave só com espaços, que era enviada e voltava como credencial recusada (exit 5). |
| Limite de entrada: `LLM_MAX_INPUT_CHARS=60000` é uma guarda de tamanho em caracteres, não uma garantia de contexto do modelo | 
| Política de credencial: `LLM_AUTH` explícito (`required` ou `none`), nunca inferido da URL | inferir pela URL; exigir chave sempre | Gateways locais podem exigir chave e servidores self-hosted remotos podem não exigir. `required` sem chave aborta antes de qualquer chamada de rede (exit 4, sem exibir valor); `none` envia um marcador interno e nunca lê `LLM_API_KEY`; 401/403 dá exit 5 e, com `none`, a mensagem sugere `LLM_AUTH=required`. |
| Testes HTTP no nível do SDK: transporte `httpx2.MockTransport` injetado com `openai.DefaultHttpxClient(transport=...)` | `respx` | O SDK `openai` 3.x usa `httpx2` e o `respx` intercepta apenas o `httpx` clássico: interceptou 0 requisições do SDK quando testado. |

## Testes e resultados

Validação completa sobre `06d5e2c` (2026-11-19). As execuções com LLM real abaixo foram feitas sobre `46106e5`.

| Teste | Entrada ou situação | Resultado esperado | Resultado obtido |
| --- | --- | --- | --- |
| Validação completa | `./scripts/validate.sh` | Todos os gates passam | 197 testes, cobertura 99,23% sobre `06d5e2c` |
| Entrada válida | `examples/exemplo.diff`; `. --commit 15a53ca` | Análise em português, exit 0 | Exit 0 nos dois, quatro seções em português |
| Reprodutibilidade | Mesmo arquivo duas vezes | Saídas iguais (não garantido) | Idênticas e iguais ao exemplo do README |
| Sem chamada real | Diff vazio com `FakeLLMClient` | Erro, fake nunca chamado | Passou, 0 chamadas |
| Argumentos inválidos | Sem `SOURCE`; `--staged --commit HEAD` | Exit 2 | Exit 2 |
| Entrada vazia ou inválida | Arquivo só com espaços, arquivo inexistente, referência git inválida | Exit 3 com orientação | Exit 3, ex.: `O diff está vazio; não há nada para analisar.` |
| Configuração inválida | `LLM_MODEL=`, `LLM_AUTH=talvez`, git fora do `PATH` | Exit 4, sem exibir valores | Exit 4, ex.: `LLM_AUTH (use required ou none)` |
| Credencial inválida | Stub responde 401 | Exit 5, chave não aparece | Exit 5, chave ausente da saída |
| Falha de comunicação | Porta fechada | Exit 6 | Exit 6: `Não foi possível falar com o LLM ...` |
| Erro do LLM | Modelo inexistente (404) e contexto excedido (400) | Exit 7 | Exit 7, mensagem de contexto |
| Credencial ausente | `LLM_AUTH=required`, chave vazia | Exit 4 | **Falhou:** traceback, exit 1. Corrigido: reexecutado em 2026-11-19 sobre `06d5e2c`, exit 4 com mensagem clara, sem traceback |
| Entrada fora do formato | Texto comum | Exit 3 | **Falhou:** exit 0, texto analisado. Permanece no código atual: verificado em 2026-11-19, o texto comum passa da validação e chega à chamada do LLM |

## Falhas e limitações

### Corte silencioso do diff

- **O que aconteceu:** com 4096 tokens de contexto no servidor local, um diff de 25367 caracteres voltou com HTTP 200 e só 2050 tokens lidos. A resposta cobriu somente parte dos arquivos.
- **Por que permanece:** o protocolo não informa o corte.

### Texto comum aceito como diff

- **O que aconteceu:** um arquivo de texto comum é aceito como entrada e enviado ao LLM; a validação rejeita apenas entrada vazia e acima de `LLM_MAX_INPUT_CHARS`.
- **Por que permanece:** a verificação de formato é planejada para o p0-intermediario.

## Problemas Resolvidos

### Segredos em mensagens de erro

- **O que aconteceu:** uma chave colada no lugar da linha `LLM_API_KEY=` inteira aparecia no erro.
- **Como foi tratado:** erros do `.env` apontam só o número da linha, e o endpoint aparece só como esquema, host e porta.
- **O que aprendi:** qualquer parte da entrada pode conter um segredo. Mensagens de erro devem usar só dados controlados pela aplicação.

### Configurações do SDK vindas do shell

- **O que aconteceu:** o SDK `openai` lê `OPENAI_CUSTOM_HEADERS`, `OPENAI_ORG_ID` e `OPENAI_PROJECT_ID` do ambiente. Um `OPENAI_CUSTOM_HEADERS` com `Authorization` substituía a chave do `.env`, e enviava essa credencial.
- **Como foi tratado:** depois de criar o cliente do SDK, organização, projeto e cabeçalhos do shell são zerados. O teste define essas variáveis e compara a requisição inteira com a de um ambiente limpo, nos dois modos de autenticação.
- **O que aprendi:** "configuração só pelo `.env`".

### Comandos do Git definidos pelo repositório

- **O que aconteceu:** ao gerar um diff de um repositório recebido como pasta, o Git podia executar comandos definidos pelo próprio repositório.
- **Como foi tratado:** a CLI desativa o monitor de arquivos, os hooks e a verificação de assinaturas durante a geração do diff. Também neutraliza filtros definidos pelo repositório, impede a busca de objetos ausentes e mostra apenas as mudanças de referência dos submódulos. No modo padrão, ainda confirma que a raiz do diretório de trabalho coincide com a entrada `.git` mais próxima.
- **Limites:** não é um isolamento completo do Git. Filtros da configuração do usuário ou do sistema ainda podem ser usados.

## Uso de IA no desenvolvimento

As Harness Oh-My-Pi (OMP) foi a principal utilizada. Claude Opus 5.5 foi o agente principal. O GPT 6.1 Sol ajudou com revisão das entregas durante as sessões, como advisor do OMP, e também em sessões separadas, como revisor independente.

| Ferramenta | Tarefa delegada | Resultado aproveitado | Correções ou alterações | Verificação |
| --- | --- | --- | --- | --- |
| Claude Opus 5.5 | Recomendar a stack | SDK `openai`, ruff, gitleaks | `respx` e `typer` descartados | Pesquisa e verificação do desenvolvedor |
| Claude Opus 5.5 | Detalhar a especificação e dividi-la em tarefas por nível | Regras de entrada, credenciais, códigos de saída e testes | stdin retirado do escopo, simplificação do código, mudança de arquitetura | Revisão do desenvolvedor |
| Claude Opus 5.5 | Implementar com TDD | Código de `src/`, testes | `cli.py` simplificado, código com provedor neutro | Testes falhando antes da mudança |
| GPT 6.1 Sol (advisor do OMP) | Revisar cada entrega durante a sessão | Testes faltando ou mal formulados (ex.: comparar o texto exato da mensagem de erro não provava que segredos ficam fora dela), validações faltando (`${VAR}` do `.env` lido do ambiente), melhorias na execução |  | Testes de regressão falhando antes da correção |
| GPT 6.1 Sol (revisor independente) | Buscar melhorias e ideias de testes mais robustos nas mudanças não commitadas | Casos de testes novos, diff preservado byte a byte, `.env` malformado, asserts frágeis |  | Testes novos bem sucedidos antes da implementação de features |

## Reflexão final

- **Principal aprendizado:**
Embora o projeto seja uma ideia bem simples, para atingir um nível de qualidade alto, considerando segurança dos dados, reproducibilidade e experiência de usuário, uma série de boas práticas tiveram que surgir. Nesse caso, mesmo tendo experiência com UV, aprendi mais sobre o uso do freeze para garantir versões fixas, sobre constantemente atualizar o AGENTS.md para dar contexto para as LLMs considerando o padrões que queria para o projeto. Também verifiquei a importância de revisar manualmente o código linha a linha para entender o que estava sendo construído, visto que muitas vezes a IA seguia em direções diferentes ao que eu estava buscando, e também vi como é mais complicado do que eu esperava garantir que informações sensíveis não apareçam nas saídas.

- **Competência mais desenvolvida:** 
Segurança de informações sensíveis, visto que primeiramente acreditava que usando somente .env as informações estariam seguras, mas pesquisando mais a fundo vi como hávia outras práticas importantes de segurança, como evitar mensagens de erros automáticas sem validação e uso de Gitleaks. 

- **Aspecto que ainda precisa ser aprimorado:** 
Um dos principais problemas é em relação a limite de contexto do servidor e possibilidade de prompt-injection. Atualmente não há mensagem de erro consistente caso o contexto do diff seja maior que do servidor, nem técnicas mais robustas para ajudar a evitar prompt-injection, embora provavelmente não seja possível resolve-lo 100%.
