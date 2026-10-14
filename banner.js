/* Destination photography and appearance controls. All assets are served locally. */
const bannerPhotos=new Map(),bannerRequests=new Map();
let bannerRenderVersion=0,bannerSearchVersion=0,bannerPreviewPhoto=null;

function rememberBannerPhotos(photos){for(const p of photos||[])bannerPhotos.set(p.id,p)}
function bannerImage(photo,x=50,y=50){
 const img=document.createElement('img');img.src=photo.image_url;img.alt='';img.style.objectPosition=`${x}% ${y}%`;img.decoding='async';img.onerror=()=>img.remove();return img;
}
function bannerCredit(photo){
 const line=document.createElement('span'),source=document.createElement('a'),licence=document.createElement('a');
 source.href=photo.source_url;source.target='_blank';source.rel='noopener noreferrer';source.textContent=`${photo.title} — ${photo.artist}`;
 licence.href=photo.licence_url;licence.target='_blank';licence.rel='noopener noreferrer';licence.textContent=photo.licence;
 line.append(source,document.createTextNode(' · '),licence,document.createTextNode(' · cropped to fit'));
 if(photo.credit&&photo.credit!=='Own work'){const extra=document.createElement('small');extra.textContent=photo.credit;line.append(extra)}
 return line;
}
async function destinationPhoto(d){
 if(d.banner_mode==='none')return null;
 const id=d.banner_mode==='photo'?d.banner_photo:d.banner_auto_photo;
 if(bannerPhotos.has(id))return bannerPhotos.get(id);
 if(d.banner_mode==='photo')return null;
 const key=`${d.id}:${d.name}:${d.country}:${d.banner_kind}`;
 if(!bannerRequests.has(key)){
  const request=api(`/api/destinations/${d.id}/banner`).then(result=>{
   if(result.photo){rememberBannerPhotos([result.photo]);d.banner_auto_photo=result.photo.id}return result.photo;
  }).catch(err=>{bannerRequests.delete(key);throw err});
  bannerRequests.set(key,request);
 }
 return bannerRequests.get(key);
}
function renderBanner(){
 const version=++bannerRenderVersion,h=holiday(),d=destination(),ds=data.destinations.filter(x=>x.holiday_id===h.id),hero=$('.hero');
 rememberBannerPhotos(data.banner_photos);
 $('#holiday-context').textContent=d?h.name:'THE WHOLE JOURNEY';
 $('#holiday-title').textContent=d?d.name:h.name;
 $('#banner-flags').textContent=d?`${flag(d.country_code)} ${d.country}`:[...new Set(ds.map(x=>flag(x.country_code)))].join(' ');
 hero.dataset.font=d?.banner_font||'skyline';
 const photographic=!d||d.banner_mode!=='none';hero.classList.toggle('photo-hero',photographic);
 if(photographic){const root=document.documentElement;root.style.setProperty('--paper','#f4f5f5');root.style.setProperty('--green','#304f5d');root.style.setProperty('--gold','#c29b61')}
 $('#banner-edit').classList.toggle('hidden',!d);$('#banner-edit').onclick=()=>{openDestination(true);$('#banner-appearance').scrollIntoView({block:'start'})};
 $('#hero-images').textContent='';$('#banner-credit-list').textContent='';$('#banner-credits').classList.add('hidden');$('#banner-message').textContent='';
 if(d){$('#holiday-dates').textContent=dateRange(d.start_date,d.end_date);$('#holiday-status').textContent=`${destinationCountdown(d)} · ${nights(d.start_date,d.end_date)} nights`}
 if(!photographic)return;
 const targets=d?[d]:ds.filter(x=>x.banner_mode!=='none').slice(0,4),loaded=new Map();
 if(!targets.length){$('#banner-message').textContent='Add a destination to discover its photographs.';return}
 $('#banner-message').textContent='Finding a view of your destination…';
 function paint(){
  if(version!==bannerRenderVersion)return;
  const images=$('#hero-images'),credits=$('#banner-credit-list');images.textContent='';credits.textContent='';
  for(const dest of targets){const photo=loaded.get(dest.id);if(!photo)continue;images.append(bannerImage(photo,dest.banner_x,dest.banner_y));credits.append(bannerCredit(photo))}
  $('#banner-credits').classList.toggle('hidden',!loaded.size);
  if(loaded.size)$('#banner-message').textContent='';
 }
 Promise.allSettled(targets.map(async dest=>{const photo=await destinationPhoto(dest);if(photo){loaded.set(dest.id,photo);paint()}})).then(()=>{
  if(version===bannerRenderVersion&&!loaded.size)$('#banner-message').textContent='No photograph loaded. Use Banner appearance to search or try again.';
 });
}
function previewBanner(){
 const form=$('#destination-form'),preview=$('#banner-preview'),mode=form.elements.banner_mode.value;
 preview.dataset.font=form.elements.banner_font.value;
 preview.querySelector('.preview-title').textContent=form.elements.name.value||'Your destination';
 const image=preview.querySelector('.preview-image');image.textContent='';
 if(mode!=='none'&&bannerPreviewPhoto)image.append(bannerImage(bannerPreviewPhoto,form.elements.banner_x.value,form.elements.banner_y.value));
 $('#banner-crop-x').textContent=`Horizontal crop: ${form.elements.banner_x.value}%`;
 $('#banner-crop-y').textContent=`Vertical crop: ${form.elements.banner_y.value}%`;
 const credit=$('#banner-preview-credit');credit.textContent='';if(mode!=='none'&&bannerPreviewPhoto)credit.append(bannerCredit(bannerPreviewPhoto));
}
function prepareBannerEditor(d){
 ++bannerSearchVersion;rememberBannerPhotos(data.banner_photos);
 const form=$('#destination-form');fill(form,{banner_mode:d?.banner_mode||'auto',banner_kind:d?.banner_kind||'recommended',banner_photo:d?.banner_photo||'',banner_font:d?.banner_font||'skyline',banner_x:d?.banner_x??50,banner_y:d?.banner_y??50});
 $('#banner-query').value='';$('#banner-results').textContent='';$('#banner-search').disabled=false;$('#banner-search-status').textContent='Search for a view, then choose a photograph. Automatic picks a suggested image after saving.';
 bannerPreviewPhoto=bannerPhotos.get(d?.banner_mode==='photo'?d.banner_photo:d?.banner_auto_photo)||null;previewBanner();
}
async function findBannerPhotos(){
 const form=$('#destination-form'),status=$('#banner-search-status'),version=++bannerSearchVersion;
 const name=form.elements.name.value.trim(),country=form.elements.country.value.trim();
 if(!name||!country){status.textContent='Enter the destination and country first.';return}
 status.textContent='Searching Wikimedia Commons…';$('#banner-results').textContent='';$('#banner-search').disabled=true;
 const query=new URLSearchParams({name,country,kind:form.elements.banner_kind.value,q:$('#banner-query').value.trim()});
 try{
  const result=await api(`/api/banner-options?${query}`);if(version!==bannerSearchVersion)return;
  rememberBannerPhotos(result.photos);status.textContent=result.photos.length?'Choose a photograph below. Your selection is saved with this destination.':'No suitable wide photographs found. Try a landmark name or a broader search.';
  if(form.elements.banner_mode.value==='auto'&&result.photos.length){bannerPreviewPhoto=result.photos[0];previewBanner()}
  for(const photo of result.photos){
   const card=document.createElement('article'),button=document.createElement('button'),title=document.createElement('span'),credit=document.createElement('div');
   card.className='banner-result';button.type='button';button.className='banner-choice';button.setAttribute('aria-label',`Choose ${photo.title}`);button.setAttribute('aria-pressed',String(form.elements.banner_photo.value===photo.id));
   const img=bannerImage(photo);img.loading='lazy';title.textContent=photo.title;button.append(img,title);credit.className='photo-credit';credit.append(bannerCredit(photo));
   button.onclick=()=>{form.elements.banner_mode.value='photo';form.elements.banner_photo.value=photo.id;bannerPreviewPhoto=photo;$('#banner-results').querySelectorAll('button').forEach(b=>b.setAttribute('aria-pressed','false'));button.setAttribute('aria-pressed','true');previewBanner()};
   card.append(button,credit);$('#banner-results').append(card);
  }
 }catch(err){if(version===bannerSearchVersion)status.textContent=err.message}
 finally{if(version===bannerSearchVersion)$('#banner-search').disabled=false}
}
function initBannerEditor(){
$('#banner-search').onclick=findBannerPhotos;
$('#banner-query').onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();findBannerPhotos()}};
for(const name of ['banner_mode','banner_font','banner_x','banner_y'])$('#destination-form').elements[name].addEventListener('input',previewBanner);
$('#destination-form').elements.name.addEventListener('input',previewBanner);
$('#destination-form').elements.banner_kind.addEventListener('change',()=>{
 ++bannerSearchVersion;$('#banner-search').disabled=false;$('#banner-results').textContent='';
 $('#destination-form').elements.banner_mode.value='auto';bannerPreviewPhoto=null;previewBanner();$('#banner-search-status').textContent='Search to preview this photo category.';
});
}
