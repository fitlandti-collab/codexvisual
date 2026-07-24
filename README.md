# codex-api

Coloca o **Codex CLI real da OpenAI** (`@openai/codex`) rodando dentro de um
container Docker, sempre no ar, com uma API HTTP por cima pra você chamar ele
por fora (com histórico/contexto de conversa mantido entre chamadas).

Como o Codex CLI é feito pra terminal (não é nativamente uma API), a ideia é:

- O container fica **sempre rodando** com uma API em FastAPI como processo
  principal (não desliga sozinho).
- Você faz **login uma única vez** (`codex login`), e a credencial fica
  salva num volume Docker — sobrevive a restart do container.
- A cada chamada na API, ela roda `codex exec` (ou `codex exec resume`) por
  baixo dos panos, num subprocess, e devolve a resposta via HTTP.

## Estrutura

```
codex-api/
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── .env.example
├── workspace/               # pasta que o Codex enxerga e onde ele trabalha
└── app/
    ├── main.py              # endpoints da API
    ├── config.py            # configurações (.env)
    ├── models.py            # schemas Pydantic
    ├── history.py           # mapeamento session_id <-> thread_id (persistido)
    └── codex_client.py       # chama o binário `codex` de verdade e parseia a saída
```

## 1. Subir o container

```bash
cp .env.example .env
docker compose up --build -d
```

Isso já deixa a API rodando em `http://localhost:8000`, mas o Codex **ainda
não está logado** — sem login, toda chamada vai falhar.

## 2. Fazer login (uma única vez)

Entre no container:

```bash
docker exec -it codex-api bash
```

Você tem duas opções de login:

### Opção A — Login com conta ChatGPT (recomendado se você tem Plus/Pro/Team)

```bash
codex login --device-auth
```

Esse modo de "device auth" imprime um código e um link. Copie o link, abra
no navegador do seu computador (não precisa ser dentro do container), digite
o código, e pronto — não depende de redirecionar porta de navegador.

*(Se preferir o fluxo normal por navegador, rode `codex login` sem flags —
nesse caso a porta 1455 já está exposta no `docker-compose.yml` para o
callback do OAuth funcionar.)*

### Opção B — Login com API Key da OpenAI

```bash
printenv OPENAI_API_KEY | codex login --with-api-key
```

(rode isso depois de ter exportado sua key com `export OPENAI_API_KEY=sk-...`
antes de entrar no container, ou digite a key manualmente no lugar da variável)

### Confirmar que logou

```bash
codex login status
exit
```

Como `/root/.codex` está num **volume Docker persistente**
(`codex_home`), você só precisa logar essa vez — mesmo derrubando e subindo
o container de novo (`docker compose down` / `up`), a sessão continua válida.

## 3. Usar o painel visual

Depois de subir o container e fazer o login, abra no navegador:

```
http://localhost:8000
```

O painel (console do operador) permite, tudo pela interface:

- **Ver o status**: se o Codex está instalado e se o login está ativo (bolinha
  verde/vermelha no topo).
- **Conversar com o Codex**: digite a instrução e mande — o contexto (thread)
  é mantido automaticamente.
- **Ver e trocar entre threads**: a lista à esquerda ("ledger") mostra todas
  as conversas já iniciadas; clique numa pra retomar, ou "+ nova thread" pra
  começar do zero.
- **Apagar threads**: passe o mouse sobre uma e clique no "×".
- **Ajustar a configuração em tempo real**: clique no ícone de engrenagem (⚙)
  no topo para abrir o painel de configuração, onde dá pra mudar:
  - `exec_flags` (sandbox e política de aprovação do Codex)
  - o timeout por chamada
  - visualizar `workspace_dir` e `codex_bin` (somente leitura)

  As mudanças em `exec_flags`/timeout são salvas na hora e valem pra próxima
  chamada — **sem precisar reiniciar o container**.

## 4. Usar a API diretamente (opcional)

Se preferir chamar via `curl`/código em vez do painel:

### Checar status

```bash
curl http://localhost:8000/health
```

Retorna se o Codex está instalado e se o login está ativo.

### Mandar uma mensagem (cria sessão automaticamente)

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Crie um arquivo hello.py que imprime Ola Mundo"}'
```

Resposta:
```json
{
  "session_id": "b3f0...",
  "thread_id": "019bd457-...",
  "reply": "Criei o arquivo hello.py com o conteúdo..."
}
```

Guarde o `session_id` — é ele que mantém o contexto.

### Continuar a mesma conversa (contexto mantido)

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id": "b3f0...", "message": "agora adicione um teste pra essa função"}'
```

O Codex vai lembrar do que foi feito antes (arquivo criado, decisões
tomadas etc.) porque a chamada usa `codex exec resume <thread_id>` por trás.

### Ver / apagar uma sessão

```bash
curl http://localhost:8000/sessions/b3f0...
curl -X DELETE http://localhost:8000/sessions/b3f0...
```

## Onde o Codex trabalha

Tudo que o Codex cria/edita fica na pasta `./workspace` (montada como volume),
ou seja, os arquivos aparecem no seu host, na pasta do projeto, em
`codex-api/workspace/`.

Se quiser apontar o Codex pra um repositório de verdade, é só colocar o
código lá dentro de `workspace/` antes de chamar a API.

## Ajustando o comportamento do Codex

No `.env`, a variável `EXEC_FLAGS` controla o sandbox e a política de
aprovação. Padrão:

```
EXEC_FLAGS=-s workspace-write -c approval_policy="never" --skip-git-repo-check
```

- `-s workspace-write`: o Codex pode ler/escrever arquivos dentro do
  workspace, mas não mexe no resto do sistema.
- `-c approval_policy="never"`: nunca para pra pedir aprovação humana
  (obrigatório aqui, já que rodamos sem terminal interativo).
- `--skip-git-repo-check`: permite rodar mesmo que `workspace/` não seja um
  repositório git.

Se quiser dar mais liberdade pro Codex (ex: acesso total ao sistema), dá pra
trocar por `--dangerously-bypass-approvals-and-sandbox` — mas isso é
literalmente perigoso, use só se souber bem o que está fazendo.

Depois de mudar o `.env`:

```bash
docker compose up -d --build
```

## Limitações conhecidas

- O parsing da resposta assume o formato de eventos `--json` da versão
  0.144.x do Codex CLI (`thread.started`, `item.completed` com
  `item_type: assistant_message`). Se você atualizar o Codex e o formato
  mudar, ajuste `app/codex_client.py` → `_parse_jsonl`.
- Cada chamada ao `/chat` roda um `codex exec` do zero (processo novo), então
  tem uma latência de inicialização — não é um servidor "quente" mantendo
  estado em memória, o contexto é recuperado via `resume` no disco.
- Timeout padrão de 600s por chamada (`EXEC_TIMEOUT_SECONDS`), ajuste se o
  Codex costuma rodar tarefas mais longas.
