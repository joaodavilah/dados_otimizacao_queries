# Publicar a análise de Power BI

## 1. Atualizar o site
Extraia o ZIP e envie a pasta `comparador-csv-supabase` na raiz do repositório. Mantenha a Vercel com Root Directory `comparador-csv-supabase` e Output Directory `dist`. A aba aparece, mas só analisa após configurar a API.

## 2. Criar a API no Render
Crie um Web Service Python apontando para o mesmo repositório:
- Root Directory: `comparador-csv-supabase/backend`
- Build Command: `pip install -r requirements.txt`
- Start Command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1`
- Health Check: `/api/health`
- Variável `PYTHON_VERSION`: `3.12.10`

Configure também:
- `PBIX_ALLOWED_ORIGINS`: URL exata do site publicado, sem barra no final. Para vários domínios, separe por vírgulas. Sem curingas.
- `SUPABASE_URL`: `https://nirnlapkeqfpaaskrrhl.supabase.co`
- `SUPABASE_PUBLISHABLE_KEY`: a chave publicável do projeto (não a service role).
- `PBIX_MAX_MB`: `50`

O backend valida a sessão Supabase antes de analisar. CORS sozinho não é autenticação. Não precisa de Groq/Gemini nem tabelas novas no Supabase.

Se usar Blueprint, o render.yaml fica dentro de backend: configure seu caminho no Render ou publique o conteúdo de backend em um repositório próprio. Para o mesmo repositório, o Web Service manual acima é mais simples.

## 3. Conectar o site
Copie a URL HTTPS atribuída à API. Edite **os dois** arquivos `pbix-config.js` e `dist/pbix-config.js`:

```js
window.PBIX_API_URL = 'https://SUA-API.onrender.com';
```

Faça commit. Aguarde a atualização da Vercel, entre no site e teste a aba Análise de Power BI.

## 4. Verificar um painel conhecido
Comece com um PBIX pequeno contendo medidas usadas, uma medida que depende de outra e uma medida sem uso. Compare com o Power BI Desktop. Sem amostra real do setor, não considerar as candidatas comprovadamente removíveis.

## Escopo desta versão
- Lê medidas, colunas, relacionamentos e expressões do modelo embutido usando pbixray 0.15.5; busca referências no layout clássico Report/Layout, incluindo os JSONs internos de configuração, consultas e filtros.
- Referência encontrada protege o item. Expressões são examinadas mesmo quando pertencem a uma medida sem uso direto: abordagem conservadora que pode preservar itens desnecessários.
- Nomes ambíguos protegem todos os itens correspondentes. Usa leitura estática por padrões, não um parser completo de DAX.
- Apenas medidas sem referências encontradas podem ser candidatas. Colunas sem referência são sempre encaminhadas para revisão devido aos usos estruturais e de tabela inteira.
- Leitura parcial, visual não coberto, grupos de cálculo ou funções DAX encaminham itens sem referências para revisão. Não interpreta todos os formatos de relatório e propriedades possíveis.
- Modelos remotos/live connection não têm inventário completo no PBIX e podem ser recusados. Outros relatórios/Excel, usos externos e Power BI Service não são consultados.
- O arquivo não é modificado. Não há exportação nem exclusão automática. A lista fica no site e é apagada em Resetar/logout.

## Recursos
Upload até 50 MB, layout até 24 MB, até 2.000 tabelas e 20.000 medidas/colunas. Uma análise por vez por processo. Até 10 tentativas por usuário por hora, contador em memória que reinicia com o serviço; não é cota persistente/global. Use um único worker.

O tamanho descompactado pode ser muito maior que o PBIX. O modo on_disk reduz uso de memória, mas não garante processamento de qualquer arquivo em um plano pequeno. A primeira versão deve ser validada com arquivos reais e recursos disponíveis do serviço. Reinícios e falta de memória podem interromper leituras. O navegador aguarda até três minutos; Resetar cancela a espera, mas a API pode continuar até concluir. Arquivos temporários são removidos ao finalizar a requisição; nunca são gravados no Supabase.

Não há promessa de hospedagem gratuita para qualquer tamanho: confira os recursos e limites do plano ao criar o serviço.
