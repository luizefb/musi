# Minha coleção de músicas

Aplicativo local para reunir MP3 do YouTube e tirar o silêncio contínuo do final. Os downloads originais são preservados. A pasta **Áudios codificados** contém as cópias prontas para o pendrive.

## Iniciar

No Windows, dê dois cliques em `Iniciar.bat`. Na primeira execução, ele instala os componentes adicionais na pasta `.deps`. Também é possível executar `python -m pip install -r requirements.txt` e `python app.py`.

O FFmpeg já está incluído em `.vendor`. Se estiver ausente, instale `imageio-ffmpeg` ou adicione o FFmpeg ao PATH.

## Fluxo do YouTube

1. Cole um ou mais links de vídeos ou playlists do YouTube, um por linha. Links do YouTube Music também são aceitos.
2. Escolha a **Pasta base** e clique em **Baixar e preparar para pendrive**.
3. O aplicativo lista vídeos das playlists, tenta baixar cada MP3 pela API **youtube-mp3-downloader** da RapidAPI, analisa o silêncio final e salva o resultado. Se a API não estiver disponível, ele baixa e converte o áudio localmente com `yt-dlp` e FFmpeg.
4. Clique em **Abrir pasta para pendrive** para conferir os arquivos antes de copiar.

Os MP3 originais ficam em `Downloads do YouTube`; os resultados ficam em `Áudios codificados`. Uma nova tentativa aproveita downloads já completos do mesmo vídeo. Falhas individuais aparecem na interface e não apagam arquivos concluídos. O botão **Cancelar** interrompe o lote após a etapa atual da API e preserva os arquivos finalizados.

A chave RapidAPI pode estar em `RAPIDAPI_KEY` ou no arquivo `.rapidapi-key` ao lado de `app.py`. O arquivo está no `.gitignore` e não deve ser compartilhado. A integração usa o servidor MCP `https://mcp.rapidapi.com` com o host `youtube-mp3-downloader.p.rapidapi.com`.

Em 14/10/2026, o provedor retornou `API not working` no diagnóstico e publicou somente uma operação sem parâmetro para URL. Por isso, o modo local automático é necessário para completar o fluxo enquanto o provedor estiver assim.

## MP3 locais e ajustes

Use **Adicionar MP3** para incluir arquivos locais. **Analisar MP3 da lista** mostra a duração, o tempo removido e o ponto de corte. **Salvar para pendrive** reúne os arquivos analisados na mesma pasta de saída. Os originais nunca são alterados. Arquivos sem silêncio final são copiados, e nomes repetidos recebem um número.

O modo **Conservador** considera silêncio abaixo de −60 dBFS, exige pelo menos 1 segundo contínuo no final e mantém mais 0,25 segundo depois do último som. **Normal** e **Forte** usam −50 e −40 dBFS. Confira o resultado antes de usar uma sensibilidade maior.

O corte copia os quadros MP3 sem recodificar, podendo variar algumas dezenas de milissegundos do tempo exibido.

## Testes

Execute `python -m unittest discover -s tests -v`.
