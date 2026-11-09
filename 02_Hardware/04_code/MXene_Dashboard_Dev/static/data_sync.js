// Local revision polling only. No broker, IBM, collection or execution endpoint.
let dashboardRevision=null,dashboardSyncPending=null;
async function synchronizeDashboard(force=false){
 if(dashboardSyncPending)return dashboardSyncPending;
 dashboardSyncPending=(async()=>{
  try{
   const probe=await fetch('/api/data-revision',{cache:'no-store'});if(!probe.ok)throw Error('Local revision unavailable');
   const revision=(await probe.json()).revision;
   if(!force&&revision===dashboardRevision)return;
   const response=await fetch('/api/dashboard-data?fresh='+String(force),{cache:'no-store'});if(!response.ok)throw Error('Canonical local refresh unavailable');
   const data=await response.json();
   if(data.snapshot.offline||data.snapshot.revision!==data.revision||data.progress.revision!==data.revision||data.records.revision!==data.revision)throw Error('Inconsistent local snapshot');
   state=data.snapshot;managerData=data.progress;recordsData=data.records;
   managerError=recordsError='';dashboardRevision=data.revision;
   $('connection').textContent='LOCAL SNAPSHOT · no automatic IBM polling';
   $('updated').textContent='Local snapshot: '+state.captured_at;
   if(active!=='User Guide')render();
  }catch(error){
   dashboardRevision=null;
   managerError=recordsError='STALE: '+error.message+'. Use Refresh local evidence to retry.';
   $('connection').textContent=managerError;
  }
 })();
 try{return await dashboardSyncPending;}finally{dashboardSyncPending=null;}
}
setInterval(()=>synchronizeDashboard(false),5000);
