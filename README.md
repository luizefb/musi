# Corte de silêncio · MP3

Aplicativo local para analisar vários MP3 e remover apenas um trecho contínuo de silêncio no final. Os arquivos originais não são alterados. Os MP3 são salvos sem recodificação, preservando a qualidade do áudio e os metadados compatíveis.

## Iniciar

No Windows, dê dois cliques em `Iniciar.bat`. Também é possível executar `python app.py` no terminal.

O projeto inclui o FFmpeg em `.vendor`. Se essa pasta não estiver presente, instale a dependência com `python -m pip install -r requirements.txt`. O aplicativo também aceita um `ffmpeg` já instalado no PATH.

## Usar

1. Clique em **Adicionar MP3** e selecione quantos arquivos quiser.
2. Clique em **Analisar arquivos**. Confira a duração, o tempo que será removido e o ponto de corte em cada linha.
3. Escolha a pasta base e clique em **Salvar áudios**. O aplicativo cria automaticamente a subpasta **Áudios codificados** e coloca nela todos os MP3 analisados. Os arquivos que precisaram de corte recebem ` - sem silencio.mp3`; os que não precisaram são copiados com ` - sem corte.mp3`.

O modo **Conservador** é o padrão. Ele considera silêncio apenas blocos abaixo de −60 dBFS, exige pelo menos 1 segundo contínuo no final e conserva mais 0,25 segundo depois do último bloco com som. **Normal** e **Forte** usam limites de −50 e −40 dBFS. Se uma música tiver um final muito baixo, confira o resultado antes de usar uma sensibilidade maior.

Se já existir um arquivo com o mesmo nome, um número é acrescentado. O aplicativo nunca substitui o original nem um resultado anterior. O corte por cópia de quadros MP3 pode variar algumas dezenas de milissegundos do tempo exibido. O nome da pasta identifica os arquivos processados; o áudio não é recodificado.

## Testes

Execute `python -m unittest discover -s tests -v`.
