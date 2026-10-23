# Agent Hub

Central local de chats com **Claude** e **Codex**: várias conversas em paralelo, agentes personalizados e aprovação de ações pela tela. Funciona sem assinatura do Copilot, usando os logins que você já tem no Claude Code e no Codex.

## Instalar em outra máquina

1. Instale o **Python 3.11+** (marque "Add Python to PATH") e o **Git**.
2. Baixe o projeto: `git clone <url-do-repositório>`.
3. **Claude:** instale a extensão Claude Code no VS Code e faça login nela uma vez (o Agent Hub usa esse login).
4. **Codex (opcional):** instale a extensão Codex/ChatGPT no VS Code e faça login.
5. **MCPs (Power BI, Superset…):** as configurações não vão no repositório, porque ficam em `~/.claude.json` e `~/.codex/config.toml` de cada máquina, assim como credenciais (ex.: `superset-mcp\credentials.xml`). Configure de novo na máquina nova e depois confira em **Ambiente → Servidores MCP**.
6. Dê dois cliques em `Iniciar.bat`. Na primeira vez ele cria o ambiente Python, instala as dependências e cria o atalho **Agent Hub**.

Chats e agentes ficam na pasta `data/`, que **não vai para o GitHub**. Para levar seus agentes, copie `data/agents.json` para a pasta `data/` da outra máquina.

## Como abrir

Dê dois cliques no atalho **Agent Hub** (pode fixá-lo na barra de tarefas ou copiá-lo para a Área de Trabalho). Ele abre o hub numa janela própria, com o ícone do Agent Hub, e liga o servidor escondido por trás. Ao fechar a última janela, o servidor se desliga sozinho; se algum chat ainda estiver trabalhando, ele termina antes. Se o atalho não abrir, o motivo fica em `data/servidor.log`.

Para ver as mensagens do servidor, use `Iniciar.bat` (modo console): ele abre em http://127.0.0.1:8787 e para quando a janela preta é fechada. Opções: `--port 9000` muda a porta, `--no-browser` não abre a janela e `--aba` abre numa aba do navegador padrão.

## O que dá para fazer

- **Vários chats ao mesmo tempo**, cada um com Claude ou Codex, modelo, esforço de raciocínio e pasta de trabalho próprios. Na barra lateral, ● indica que o chat está trabalhando e ⏳ que está aguardando você.
- **Aprovações na tela**: comandos, edições de arquivo e chamadas de ferramenta aparecem com as opções *Permitir*, *Permitir sempre (nesta sessão)* e *Recusar*. Perguntas de múltipla escolha e aprovação de plano também aparecem ali.
- **Agentes**: guardam instruções, IA, modelo, permissões e pasta padrão. Um agente Claude pode chamar outros agentes Claude como subagentes. O botão *Exportar para ~/.claude/agents* deixa o agente disponível também no Claude Code do VS Code.
- **Tudo do Claude Code**: CLAUDE.md, skills, comandos `/` (digite `/` para ver a lista), subagentes, hooks e servidores MCP da sua configuração.
- **Tudo do Codex**: config.toml (modelo padrão, MCP como o superset-herval), AGENTS.md, plano de tarefas e envio de mensagem durante a execução (a mensagem é incorporada ao turno em andamento).
- **Retomar conversas**: o histórico fica salvo. Ao reabrir um chat, a sessão continua de onde parou.
- **Importar conversas do VS Code**: em *⤓ Importar conversas* aparecem as conversas do Claude Code e do Codex (VS Code ou linha de comando). Ao importar, o histórico vem para a tela. Na primeira mensagem enviada daqui, o Agent Hub cria uma **cópia** (fork) com todo o contexto, e a original continua intacta no VS Code.
- **Monitor de MCPs**: em *Ambiente → Servidores MCP* aparecem os MCPs do Claude e do Codex com o status de cada um. MCPs que rodam de dentro de extensões do VS Code (como o Power BI) têm a versão no caminho. Quando a extensão atualiza, o servidor aparece como desatualizado ou quebrado e o botão **Corrigir** aponta para a versão instalada, com backup do arquivo antes. Ao abrir o app, um alerta ⚠ avisa se houver problema.

## Permissões

| Claude | O que faz |
|---|---|
| Perguntar antes de agir | Pede aprovação para editar arquivos, rodar comandos etc. (padrão) |
| Aceitar edições automaticamente | Edita arquivos sem perguntar, mas pergunta antes de rodar comandos |
| Modo plano | Só planeja e pede aprovação do plano |
| Auto | Um classificador decide o que é seguro |
| Sem perguntar nada | Executa tudo sem perguntar. Use só em pastas descartáveis |

| Codex: sandbox | Codex: aprovação |
|---|---|
| Somente leitura / Pode editar a pasta do chat / Acesso total | Pedir quando precisar / Pedir para quase tudo / Nunca pedir |

**Sandbox do Windows (Codex):** enquanto ele estiver *não configurado* (veja em **Ambiente**), o Codex roda em modo somente leitura e pede aprovação para qualquer comando. Para configurar, vá em **Ambiente → Configurar sandbox**. O modo "elevado" pede confirmação de administrador (UAC).

## Onde ficam os dados

- `data/chats.json` e `data/agents.json`: lista de chats e agentes.
- `data/chats/<id>.jsonl`: histórico exibido na tela.
- As sessões em si ficam em `~/.claude/projects` (Claude) e `~/.codex/sessions` (Codex), como no VS Code.

Apagar um chat no Agent Hub remove só o histórico deste app.

## Requisitos

- Python 3.11+ (o `Iniciar.bat` cria o ambiente sozinho na primeira vez).
- Login feito no Claude Code (`~/.claude`). O SDK traz o próprio executável do Claude.
- Codex: extensão do Codex/ChatGPT no VS Code (o `codex.exe` é encontrado sozinho), `codex` no PATH ou a variável `CODEX_PATH`.

## Estrutura

```
server.py           API HTTP + WebSocket (FastAPI)
hub/sessions.py     Sessões Claude (Agent SDK) e Codex (codex app-server), aprovações, eventos
hub/providers.py    Descoberta dos executáveis e modelos
hub/store.py        Persistência em JSON
static/             Interface (HTML/CSS/JS, sem build)
agentes/            Pastas de trabalho dos agentes (manual, ferramentas e modelos de cada um)
  conferencia-faturamento/   Conferente de Faturamento + extratores (Power BI × Superset)
```

Os agentes não deixam arquivos de resultado nas pastas: o que é intermediário fica em
`%TEMP%\agent-hub\` e se apaga sozinho depois de 2 dias. Relatórios e planilhas só são gerados quando você pede.

Por segurança, a API só aceita conexões do próprio computador (127.0.0.1) e recusa pedidos vindos de outros sites.
