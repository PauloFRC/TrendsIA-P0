# Changelog

## p0-basico (tag)

Primeira versão (tag `p0-basico`): a CLI envia um diff a um LLM e imprime uma análise. Mais detalhes no RELATORIO_EXECUCAO.md.

### Adicionado

- Comando `diff-analyzer SOURCE`, em que `SOURCE` é um arquivo `.diff` ou um repositório git.
- Modos git: sem opção, `git diff HEAD`; `--staged`, `--commit REF` e `--range A..B`.
- Prompt padrão em markdown no pacote e, para uma execução, `-p TEXTO` ou `--prompt-file ARQUIVO`.
- Configuração só pelo `.env`, com modelo em `.env.example`: `LLM_BASE_URL`, `LLM_MODEL`, `LLM_AUTH`, `LLM_API_KEY`, `LLM_TEMPERATURE`, `LLM_SEED` e `LLM_MAX_OUTPUT_TOKENS`.
- Códigos de saída de 2 a 7, cada um com mensagem definida.
- Ajuda e erros de argumentos em português.
- O git não executa comandos configurados pelo repositório analisado: fsmonitor, hooks, filtros, verificação de assinatura, download de objetos ausentes e diff externo em submódulos.
- No modo padrão, a CLI rejeita o repositório se o Git indicar uma raiz diferente da raiz do checkout selecionado.
- `Dockerfile` e `.dockerignore`: a imagem analisa `examples/exemplo.diff` com a configuração de `.env.container`.
- `LLM_TLS_VERIFY` no `.env`: `false` desliga a verificação do certificado TLS do endpoint. Default é `true`.

### Limitações conhecidas

- `LLM_TIMEOUT_CONNECT`, `LLM_TIMEOUT_READ`, `LLM_MAX_INPUT_CHARS` e `LLM_DATA_EGRESS` são validadas, mas ainda não têm efeito.
- Texto que não é um diff é enviado e analisado, com exit 0.
- Alguns servidores cortam uma entrada maior que o contexto sem avisar e a ferramenta não detecta.
- Possibilidade de injeção de prompt.
- Filtros definidos só no repositório analisado não são aplicados, submódulos com mudanças locais não aparecem como `-dirty` e, num clone parcial, um objeto ausente gera erro em vez de ser baixado.
