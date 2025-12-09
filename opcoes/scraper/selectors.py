BASE_URL = "https://opcoes.net.br/opcoes/bovespa"

# Selectors utilizados na página
SELECT_ID_ACAO = 'select[name="IdAcao"]'
SELECT_ID_LISTA = 'select[name="IdLista"]'
SELECT_ALL_TYPES_RADIO = '#tpTodas'
SELECT_ALL_TYPES_LABEL = 'label[for="tpTodas"]'
SELECT_CALLS_CHECKBOX = '#tpCalls'
SELECT_CALLS_LABEL = 'label[for="tpCalls"]'

VENCIMENTOS_CONTAINER = '#listavencimentos'
VENCIMENTOS_CHECKBOXES = '#listavencimentos input[type="checkbox"]'

SLIDER_STRIKE_TRACK = '#strike-range'
SLIDER_STRIKE_HANDLES = '#strike-range .ui-slider-handle'

TABELA_ID = '#tblListaOpc'
TABELA_LENGTH = '#tblListaOpc_length select'
TABELA_TBODY_ROWS = '#tblListaOpc tbody tr'
TABELA_NEXT = '#tblListaOpc_next'

# Filtro por modalidade (A/E) na tabela (DataTables header filter)
# DataTables cria duas instâncias desse select (head fixo + tabela), então limitamos ao thead.
SELECT_MOD_FILTER = '#tblListaOpc thead select[data-dtcolindex="4"]'
