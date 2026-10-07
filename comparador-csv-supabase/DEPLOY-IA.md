# Publicar a integração de IA

1. Extraia o ZIP e envie a pasta `comparador-csv-supabase` inteira na raiz do repositório. Preserve a pasta `api` ao lado de `dist`; não coloque o backend dentro de `dist`.
2. Na Vercel, mantenha Root Directory `comparador-csv-supabase`, preset Other, Output Directory `dist` e Build Command vazio. O arquivo `vercel.json` configura a função Node e a saída estática.
3. Cadastre em Production as quatro variáveis já solicitadas: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `GROQ_API_KEY`, `GEMINI_API_KEY`. Para testar deployments Preview, cadastre também nesse ambiente. Nunca coloque os valores no GitHub.
4. O Supabase precisa da tabela `ai_limits`, tabela `ai_usage` e função `reserve_ai_usage` do SQL fornecido nesta conversa. A função só pode ser executada por service_role. Não crie políticas públicas para essas tabelas.
5. Aguarde um novo deploy. Teste Off com `SELECT id FROM pedidos;`, depois Groq e Gemini com a mesma consulta ilustrativa. A query não é executada.

## Fluxo e limites

O backend verifica o token do usuário no Supabase, refaz as regras e bloqueia estrutura ou escopo inválido. Não confia na nota, identidade ou alertas enviados pelo navegador. O prompt é fixo e permanece no backend; instruções dentro da consulta são tratadas como dados.

O contador reserva cada tentativa atomicamente no banco, incluindo um orçamento conservador: bytes UTF-8 do prompt e da query, margem e até 1.800 tokens de saída. Reservas não são devolvidas após falhas, porque a chamada pode ter sido processada. Esse mecanismo reduz o uso disponível para priorizar margem. Não há retry nem troca automática de fornecedor.

O banco usa janelas móveis de 60 segundos e 24 horas. A cota é compartilhada entre Production e Preview se ambos usarem o mesmo Supabase. Não controla usos externos da mesma conta de IA; o fornecedor pode responder 429 mesmo com orçamento interno disponível. Para evitar cobrança, mantenha as contas em seus planos gratuitos; o contador do site não substitui controles de faturamento do fornecedor.

Groq: GPT-OSS 120B, até 7.000 tokens reservados por tentativa com a configuração inicial. Queries longas recebem orientação para escolher Gemini ou Off. Gemini: 3.1 Flash Lite, até 200.000 tokens reservados por tentativa. O teto interno diário do Gemini é uma escolha do aplicativo, não uma cota diária de tokens confirmada pelo fornecedor.

O SQL é enviado ao fornecedor selecionado. A aplicação não salva queries nem respostas em tabelas; guarda contagens de uso. As políticas do fornecedor continuam aplicáveis. As regras locais funcionam quando a IA falha.

## Validação feita

Sintaxe JavaScript e oito cenários de backend com chamadas simuladas: ambos os fornecedores, falta de login, estrutura bloqueada, cota interna esgotada, 429 do fornecedor, orçamento excedido e JSON inválido. A conexão real depende das variáveis, migração e deploy do usuário; não foi executada com chaves reais.
