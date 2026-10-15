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

## Escopo desta versão (v14)
- Lê o inventário lógico de tabelas, medidas e colunas no modelo embutido, incluindo colunas de tabelas calculadas e DirectQuery quando os metadados estão no arquivo. A lista de arquivos VertiPaq não é usada como inventário completo.
- Cruza IDs de relacionamentos, hierarquias, ordenação e agrupamento. Ordenação e agrupamento são dependências do objeto proprietário: se a coluna proprietária é usada, sua dependência também é preservada. Participação em hierarquias, relacionamentos, segurança, calendários, variações, agregações, chaves e objetos internos são preservados conservadoramente como usos estruturais.
- Lê expressões de medidas, colunas, tabelas calculadas, RLS, formatação dinâmica, linhas de detalhe e KPIs. Liga formatação e linhas de detalhe à medida proprietária quando esse vínculo está disponível. KPIs preservam sua medida e dependências. Referências de tabelas/definições sem proprietário reconhecido são preservadas conservadoramente.
- Usa tokens DAX para separar comentários, strings, tabelas qualificadas, identificadores escapados e nomes temporários de construtores de tabelas. Não executa DAX e não é um parser semântico completo. Uso de tabela inteira preserva suas colunas conservadoramente, mesmo quando nem todas são necessárias ao cálculo.
- Procura o layout clássico e as partes PBIR reconhecidas. Busca referências estruturadas em filtros, consultas, formatação, bookmarks e páginas ocultas presentes. Cruza aliases por consulta, hierarquias e variações. Texto de título e correspondência parcial de nome não confirmam uso.
- Referências exatas formam o grafo de dependências. Referências ambíguas ficam em Analisar e a dúvida se propaga para suas dependências conhecidas. Referências DAX não resolvidas em um cálculo com proprietário identificado são sinalizadas nesse cálculo e suas dependências; não bloqueiam automaticamente objetos independentes. Leitura do modelo/relatório incompleta, visuais não cobertos e expressões ilegíveis continuam bloqueando conclusões negativas no escopo afetado.
- Grupos de cálculo e funções DAX ainda podem conter referências dinâmicas e continuam exigindo revisão. Outros relatórios/Excel, usos externos e Power BI Service não são consultados. Modelos remotos sem inventário embutido podem ser recusados.
- Cada item pode mostrar a cadeia de uso, quem o referencia e o motivo específico de uma dúvida. Não é uma porcentagem de confiança nem garantia de ausência de usos não implementados.
- O arquivo não é modificado. Não há exportação nem exclusão automática. A lista fica no site e é apagada em Resetar/logout.

## Recursos
Upload até 50 MB, layout até 24 MB, até 2.000 tabelas e 20.000 medidas/colunas. Uma análise por vez por processo. Até 10 tentativas por usuário por hora, contador em memória que reinicia com o serviço; não é cota persistente/global. Use um único worker.

O tamanho descompactado pode ser muito maior que o PBIX. O modo on_disk reduz uso de memória, mas não garante processamento de qualquer arquivo em um plano pequeno. A primeira versão deve ser validada com arquivos reais e recursos disponíveis do serviço. Reinícios e falta de memória podem interromper leituras. O navegador aguarda até três minutos; Resetar cancela a espera, mas a API pode continuar até concluir. Arquivos temporários são removidos ao finalizar a requisição; nunca são gravados no Supabase.

Não há promessa de hospedagem gratuita para qualquer tamanho: confira os recursos e limites do plano ao criar o serviço.

## Atualização v13: cobertura ampliada
A leitura agora procura Report/Layout, Report/report.json e as partes Report/definition/*.json (PBIR) reconhecidas dentro do PBIX. Todas as partes JSON são percorridas, incluindo configurações de visual, filtros, formatação e bookmarks presentes. Arquivos malformados/incompletos continuam bloqueando conclusões negativas. Não há garantia de todos os formatos internos de PBIX.

Dependências DAX qualificadas usam o nome da tabela; nomes ambíguos são preservados conservadoramente. Uso de tabela inteira como argumento de funções protege suas colunas. IDs de colunas são cruzados com tabelas para reconhecer ordenação, níveis de hierarquia, agrupamentos, variações, permissões, colunas relacionadas, alternativas de agregação e referências de calendário quando disponíveis. Colunas sem referência podem ser classificadas como Não é usado ou Usado por não usado quando essa leitura estrutural é confirmada.

A API mudou: aguarde o deploy do Render após atualizar o GitHub e execute uma nova análise. A definição Não é usado se limita às referências encontradas pelos leitores implementados; não equivale a cobertura total do Measure Killer. Os testes incluem layouts sintéticos e dependências simuladas; ainda é necessário validar com PBIX reais e comparar com resultados conhecidos. Grupos de cálculo, funções DAX e KPIs continuam exigindo revisão complementar.

## Atualização v14: metadados lógicos e evidências de uso
Atualize a pasta inteira no GitHub, incluindo os novos arquivos `backend/app/services/model_metadata.py` e `backend/app/services/dax_dependencies.py`. Não precisa mudar as variáveis de ambiente, criar tabelas no Supabase ou configurar IA.

Aguarde os deploys da Vercel e do Render. Faça uma nova análise: resultados anteriores não são reprocessados. A API retorna `analysis_version: 14`; se o site receber uma versão anterior, avisa para aguardar a atualização do Render.

O adaptador de metadados usa uma conexão interna da versão fixada pbixray 0.15.5 para ler propriedades que os endpoints públicos omitem. Se esse adaptador ou o esquema não puder ser lido, o inventário alternativo não autoriza conclusões de ausência de uso. Não atualize pbixray sem validar o adaptador.

Validação: testes com layouts JSON, banco SQLite de metadados simulado, grafo de dependências, endpoints com autenticação simulada e interface em DOM simulado. Ainda não foi validado um PBIX real do setor, nem a abertura/comparação dos resultados no Power BI Desktop. Antes de usar a lista para remover objetos, compare um painel com uso conhecido.
