const http = require('http');
http.createServer(async (req,res) => {
  let raw='';if (!req.body) { for await (const chunk of req) raw+=chunk; }
  res.setHeader('Content-Type','application/json');
  if (req.method==='POST' && req.url==='/v1/chat/completions') {
    const request=req.body || JSON.parse(raw);
    const en=JSON.stringify(request.messages).includes('natural English');
    const content=JSON.stringify(en ? {analysis:['Your four favourites suggest an interest in characters at a turning point.','Atmosphere and emotional tension appear to matter to you as much as the story.'],personality:'This is a first reading from four favourites, and will grow with your notes.'} : {analysis:['Dört favorin, dönüm noktasındaki karakterlere ilgi duyduğunu düşündürüyor.','Hikâye kadar atmosfer ve duygusal gerilim de seçimlerinde yer buluyor.'],personality:'Bu ilk okuma dört favorine dayanıyor; yeni notlarınla birlikte gelişecek.'});
    return res.end(JSON.stringify({id:'simulation-taste',object:'chat.completion',created:1,model:request.model,choices:[{index:0,message:{role:'assistant',content},finish_reason:'stop'}],usage:{prompt_tokens:100,completion_tokens:80,total_tokens:180}}));
  }
  res.statusCode=501;res.end(JSON.stringify({error:'Not modeled by native onboarding simulation'}));
}).listen(process.env.PORT || 4519);
