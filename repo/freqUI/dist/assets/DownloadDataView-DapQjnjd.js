import{$ as e,I as t,L as n,N as r,Q as i,Rt as a,T as o,_t as s,at as c,c as l,d as u,g as d,h as f,it as p,l as m,lt as h,m as g,r as _,s as v,u as y}from"./runtime-core.esm-bundler-Dz8W7G7L.js";import{i as b,l as x}from"./inputtext-CGXWSxsO.js";import{_ as S,g as C,jt as w,l as T}from"./settings-zuPpBhWo.js";import{E,O as D,n as O,o as k}from"./ftbotwrapper-Bj8L2PBl.js";import{n as ee,r as te,t as A}from"./pairlistConfig-C_YCp6vP.js";import{t as j}from"./message-DBhljmAq.js";import{t as ne}from"./TimeRangeSelect-BkONOaYO.js";import{L as M,P as N,U as P,V as F,z as I}from"./index-D0kNPN8i.js";import{t as L}from"./inputnumber-CwcszRs-.js";import{t as R}from"./DraggableContainer-CFYIB3Yr.js";import{t as z}from"./check-DHNnovCH.js";import{t as B}from"./multiselect-DahKPErb.js";import{t as V}from"./ExchangeSelect-DXnVx2nO.js";var H=S.extend({name:`progressbar`,style:`
    .p-progressbar {
        display: block;
        position: relative;
        overflow: hidden;
        height: dt('progressbar.height');
        background: dt('progressbar.background');
        border-radius: dt('progressbar.border.radius');
    }

    .p-progressbar-value {
        margin: 0;
        background: dt('progressbar.value.background');
    }

    .p-progressbar-label {
        color: dt('progressbar.label.color');
        font-size: dt('progressbar.label.font.size');
        font-weight: dt('progressbar.label.font.weight');
    }

    .p-progressbar-determinate .p-progressbar-value {
        height: 100%;
        width: 0%;
        position: absolute;
        display: none;
        display: flex;
        align-items: center;
        justify-content: center;
        overflow: hidden;
        transition: width 1s ease-in-out;
    }

    .p-progressbar-determinate .p-progressbar-label {
        display: inline-flex;
    }

    .p-progressbar-indeterminate .p-progressbar-value::before {
        content: '';
        position: absolute;
        background: inherit;
        inset-block-start: 0;
        inset-inline-start: 0;
        inset-block-end: 0;
        will-change: inset-inline-start, inset-inline-end;
        animation: p-progressbar-indeterminate-anim 2.1s cubic-bezier(0.65, 0.815, 0.735, 0.395) infinite;
    }

    .p-progressbar-indeterminate .p-progressbar-value::after {
        content: '';
        position: absolute;
        background: inherit;
        inset-block-start: 0;
        inset-inline-start: 0;
        inset-block-end: 0;
        will-change: inset-inline-start, inset-inline-end;
        animation: p-progressbar-indeterminate-anim-short 2.1s cubic-bezier(0.165, 0.84, 0.44, 1) infinite;
        animation-delay: 1.15s;
    }

    @keyframes p-progressbar-indeterminate-anim {
        0% {
            inset-inline-start: -35%;
            inset-inline-end: 100%;
        }
        60% {
            inset-inline-start: 100%;
            inset-inline-end: -90%;
        }
        100% {
            inset-inline-start: 100%;
            inset-inline-end: -90%;
        }
    }
    @-webkit-keyframes p-progressbar-indeterminate-anim {
        0% {
            inset-inline-start: -35%;
            inset-inline-end: 100%;
        }
        60% {
            inset-inline-start: 100%;
            inset-inline-end: -90%;
        }
        100% {
            inset-inline-start: 100%;
            inset-inline-end: -90%;
        }
    }

    @keyframes p-progressbar-indeterminate-anim-short {
        0% {
            inset-inline-start: -200%;
            inset-inline-end: 100%;
        }
        60% {
            inset-inline-start: 107%;
            inset-inline-end: -8%;
        }
        100% {
            inset-inline-start: 107%;
            inset-inline-end: -8%;
        }
    }
    @-webkit-keyframes p-progressbar-indeterminate-anim-short {
        0% {
            inset-inline-start: -200%;
            inset-inline-end: 100%;
        }
        60% {
            inset-inline-start: 107%;
            inset-inline-end: -8%;
        }
        100% {
            inset-inline-start: 107%;
            inset-inline-end: -8%;
        }
    }
`,classes:{root:function(e){var t=e.instance;return[`p-progressbar p-component`,{"p-progressbar-determinate":t.determinate,"p-progressbar-indeterminate":t.indeterminate}]},value:`p-progressbar-value`,label:`p-progressbar-label`}}),U={name:`ProgressBar`,extends:{name:`BaseProgressBar`,extends:C,props:{value:{type:Number,default:null},mode:{type:String,default:`determinate`},showValue:{type:Boolean,default:!0}},style:H,provide:function(){return{$pcProgressBar:this,$parentInstance:this}}},inheritAttrs:!1,computed:{progressStyle:function(){return{width:this.value+`%`,display:`flex`}},indeterminate:function(){return this.mode===`indeterminate`},determinate:function(){return this.mode===`determinate`},dataP:function(){return w({determinate:this.determinate,indeterminate:this.indeterminate})}}},W=[`aria-valuenow`,`data-p`],G=[`data-p`],K=[`data-p`],q=[`data-p`];function J(e,t,i,s,c,l){return r(),u(`div`,o({role:`progressbar`,class:e.cx(`root`),"aria-valuemin":`0`,"aria-valuenow":e.value,"aria-valuemax":`100`,"data-p":l.dataP},e.ptmi(`root`)),[l.determinate?(r(),u(`div`,o({key:0,class:e.cx(`value`),style:l.progressStyle,"data-p":l.dataP},e.ptm(`value`)),[e.value!=null&&e.value!==0&&e.showValue?(r(),u(`div`,o({key:0,class:e.cx(`label`),"data-p":l.dataP},e.ptm(`label`)),[n(e.$slots,`default`,{},function(){return[g(a(e.value+`%`),1)]})],16,K)):y(``,!0)],16,G)):l.indeterminate?(r(),u(`div`,o({key:1,class:e.cx(`value`),"data-p":l.dataP},e.ptm(`value`)),null,16,q)):y(``,!0)],16,W)}U.render=J;var Y={viewBox:`0 0 24 24`,width:`1.2em`,height:`1.2em`};function X(e,t){return r(),u(`svg`,Y,[...t[0]||=[l(`path`,{fill:`currentColor`,d:`M8 17v-2h8v2zm8-7l-4 4l-4-4h2.5V7h3v3zM5 3h14a2 2 0 0 1 2 2v14c0 1.11-.89 2-2 2H5a2 2 0 0 1-2-2V5c0-1.1.9-2 2-2m0 2v14h14V5z`},null,-1)]])}var Z=c({name:`mdi-download-box-outline`,render:X}),Q={class:`flex flex-row items-end gap-1`},re={class:`ms-2 w-full grow space-y-1`},ie=[`title`],$={key:1},ae={class:`flex justify-between`},oe={key:1},se={key:2,class:`w-25`},ce={key:3,class:`flex flex-col md:flex-row w-full grow gap-2`},le=d({__name:`BackgroundJobTracking`,setup(e){let{runningJobs:n,clearJobs:o}=k();return(e,c)=>{let d=Z,p=z,h=U,v=N,b=T;return r(),u(`div`,Q,[l(`ul`,re,[(r(!0),u(_,null,t(s(n),(e,n)=>(r(),u(`li`,{key:n,class:`border p-1 pb-2 rounded-sm dark:border-surface-700 border-surface-300 flex gap-2 items-center`,title:n},[e.taskStatus?.job_category===`download_data`?(r(),m(d,{key:0})):(r(),u(`span`,$,a(e.taskStatus?.job_category),1)),l(`div`,ae,[e.taskStatus?.status===`success`?(r(),m(p,{key:0,class:`text-success`,title:``})):(r(),u(`span`,oe,a(e.taskStatus?.status),1)),e.taskStatus?.progress?(r(),u(`span`,se,a(e.taskStatus?.progress),1)):y(``,!0)]),e.taskStatus?.progress?(r(),m(h,{key:2,class:`w-full grow`,value:e.taskStatus?.progress/100*100,"show-progress":``,max:100,striped:``},null,8,[`value`])):y(``,!0),e.taskStatus?.progress_tasks?(r(),u(`div`,ce,[(r(!0),u(_,null,t(Object.entries(e.taskStatus?.progress_tasks),([t,n])=>(r(),u(`div`,{key:t,class:`w-full`},[g(a(n.description)+` `,1),f(h,{class:`w-full grow`,value:Math.round(n.progress/n.total*100*100)/100,"show-progress":``,pt:{value:{class:e.taskStatus.status===`success`?`bg-emerald-500`:`bg-amber-500`}},striped:``},null,8,[`value`,`pt`])]))),128))])):y(``,!0)],8,ie))),128))]),Object.keys(s(n)).length>0?(r(),m(b,{key:0,severity:`secondary`,class:`ms-auto`,onClick:s(o)},{icon:i(()=>[f(v)]),_:1},8,[`onClick`])):y(``,!0)])}}}),ue=h([{description:`All USDT Pairs`,pairs:[`.*/USDT`]},{description:`All USDT Futures Pairs`,pairs:[`.*/USDT:USDT`]}]);function de(){return{pairTemplates:v(()=>ue.value.map((e,t)=>({...e,idx:t})))}}var fe={class:`px-1 mx-auto w-full max-w-4xl lg:max-w-7xl`},pe={class:`flex mb-3 gap-3 flex-col`},me={class:`flex flex-col gap-3`},he={class:`flex flex-col lg:flex-row gap-3`},ge={class:`flex-fill`},_e={class:`flex flex-col gap-2`},ve={class:`flex gap-2`},ye={class:`flex flex-col gap-1`},be={class:`flex flex-col gap-1`},xe={class:`flex-fill px-3`},Se={class:`flex flex-col gap-2`},Ce={class:`px-3 border dark:border-surface-700 border-surface-300 p-2 rounded-sm`},we={class:`flex flex-col gap-2`},Te={class:`flex justify-between items-center`},Ee={key:0},De={key:1,class:`flex items-center gap-2`},Oe={class:`mb-2 border dark:border-surface-700 border-surface-300 rounded-sm p-2 text-start`},ke={class:`mb-2 border dark:border-surface-700 border-surface-300 rounded-md p-2 text-start`},Ae={class:`grid grid-cols md:grid-cols-2 items-center gap-2`},je={class:`mb-2 border dark:border-surface-700 border-surface-300 rounded-md p-2 text-start`},Me={class:`px-3`},Ne=d({__name:`DownloadDataMain`,setup(n){let o=O(),c=A(),d=h([`BTC/USDT`,`ETH/USDT`,``]),v=h([`5m`,`1h`]),S=h({useCustomTimerange:!1,timerange:``,days:30}),{pairTemplates:C}=de(),w=h({customExchange:!1,selectedExchange:{exchange:`binance`,trade_mode:{margin_mode:E.NONE,trading_mode:D.SPOT}}}),k=h({erase:!1,prepend_data:!1,downloadTrades:!1,candleTypes:[]}),N=h(!1),P=[{text:`Spot`,value:`spot`},{text:`Futures`,value:`futures`},{text:`Funding rate`,value:`funding_rate`},{text:`Giá mark`,value:`mark`},{text:`Chỉ số`,value:`index`},{text:`Premium index`,value:`premiumIndex`}];function z(e){d.value.push(...e)}function H(e){d.value=[...e]}async function U(){let e={pairs:d.value.filter(e=>e!==``),timeframes:v.value.filter(e=>e!==``)};S.value.useCustomTimerange&&S.value.timerange?e.timerange=S.value.timerange:e.days=S.value.days,N.value&&(e.erase=k.value.erase,e.download_trades=k.value.downloadTrades,w.value.customExchange&&(e.exchange=w.value.selectedExchange.exchange,e.trading_mode=w.value.selectedExchange.trade_mode.trading_mode,e.margin_mode=w.value.selectedExchange.trade_mode.margin_mode),o.activeBot.botFeatures.downloadDataCandleTypes&&k.value.candleTypes.length>0&&(e.candle_types=k.value.candleTypes),o.activeBot.botFeatures.downloadDataPrepend&&k.value.prepend_data&&(e.prepend_data=!0)),await o.activeBot.startDataDownload(e)}return(n,h)=>{let E=le,D=ee,O=T,A=F,W=I,G=ne,K=L,q=M,J=te,Y=j,X=B,Z=V,Q=R;return r(),u(`div`,fe,[f(E,{class:`mb-4`}),f(Q,{header:`Tải dữ liệu`,class:`mx-1 p-4`},{default:i(()=>[l(`div`,pe,[l(`div`,me,[l(`div`,he,[l(`div`,ge,[l(`div`,_e,[h[14]||=l(`div`,{class:`flex justify-between`},[l(`h4`,{class:`text-start font-bold text-lg`},`Chọn cặp giao dịch`),l(`h5`,{class:`text-start font-bold text-lg`},`Cặp từ mẫu`)],-1),l(`div`,ve,[f(D,{modelValue:s(d),"onUpdate:modelValue":h[0]||=e=>p(d)?d.value=e:null,placeholder:`Cặp`,size:`small`,class:`grow`},null,8,[`modelValue`]),l(`div`,ye,[l(`div`,be,[(r(!0),u(_,null,t(s(C),e=>(r(),m(O,{key:e.idx,severity:`secondary`,title:e.pairs.reduce((e,t)=>`${e}${t}\n`,``),onClick:t=>z(e.pairs)},{default:i(()=>[g(a(e.description),1)]),_:2},1032,[`title`,`onClick`]))),128))]),f(A),f(O,{disabled:s(c).whitelist.length===0,title:`Dùng tất cả cặp từ cấu hình Pairlist - yêu cầu pairlist đã chạy trước.`,severity:`secondary`,onClick:h[1]||=e=>H(s(c).whitelist)},{default:i(()=>[...h[13]||=[g(` Dùng cặp từ cấu hình Pairlist `,-1)]]),_:1},8,[`disabled`])])])])]),l(`div`,xe,[l(`div`,Se,[h[15]||=l(`h4`,{class:`text-start font-bold text-lg`},`Chọn khung thời gian`,-1),f(D,{modelValue:s(v),"onUpdate:modelValue":h[2]||=e=>p(v)?v.value=e:null,placeholder:`Khung thời gian`},null,8,[`modelValue`])])])]),l(`div`,Ce,[l(`div`,we,[l(`div`,Te,[h[17]||=l(`h4`,{class:`text-start mb-0 font-bold text-lg`},`Chọn thời gian`,-1),f(W,{modelValue:s(S).useCustomTimerange,"onUpdate:modelValue":h[3]||=e=>s(S).useCustomTimerange=e,class:`mb-0`,switch:``},{default:i(()=>[...h[16]||=[g(` Dùng khoảng thời gian tùy chỉnh `,-1)]]),_:1},8,[`modelValue`])]),s(S).useCustomTimerange?(r(),u(`div`,Ee,[f(G,{modelValue:s(S).timerange,"onUpdate:modelValue":h[4]||=e=>s(S).timerange=e},null,8,[`modelValue`])])):(r(),u(`div`,De,[h[18]||=l(`label`,null,`Số ngày tải:`,-1),f(K,{modelValue:s(S).days,"onUpdate:modelValue":h[5]||=e=>s(S).days=e,type:`number`,"aria-label":`Số ngày tải`,min:1,step:1,size:`small`},null,8,[`modelValue`])]))])]),l(`div`,Oe,[f(O,{class:`mb-2`,severity:`secondary`,onClick:h[6]||=e=>N.value=!s(N)},{default:i(()=>[h[19]||=g(` Tùy chọn nâng cao `,-1),s(N)?(r(),m(J,{key:1})):(r(),m(q,{key:0}))]),_:1}),f(b,null,{default:i(()=>[e(l(`div`,null,[f(Y,{severity:`info`,class:`mb-2 py-2`},{default:i(()=>[...h[20]||=[g(` Tùy chọn nâng cao (Xóa dữ liệu, Tải giao dịch, Cài đặt sàn tùy chỉnh) chỉ áp dụng khi phần này đang mở rộng. `,-1)]]),_:1}),l(`div`,ke,[f(W,{modelValue:s(k).erase,"onUpdate:modelValue":h[7]||=e=>s(k).erase=e,class:`mb-2`},{default:i(()=>[...h[21]||=[g(`Xóa dữ liệu hiện tại`,-1)]]),_:1},8,[`modelValue`]),s(o).activeBot.botFeatures.downloadDataPrepend?(r(),m(W,{key:0,modelValue:s(k).prepend_data,"onUpdate:modelValue":h[8]||=e=>s(k).prepend_data=e,class:`mb-2`},{default:i(()=>[...h[22]||=[g(`Chèn dữ liệu khi tải`,-1)]]),_:1},8,[`modelValue`])):y(``,!0),f(W,{modelValue:s(k).downloadTrades,"onUpdate:modelValue":h[9]||=e=>s(k).downloadTrades=e,class:`mb-2`},{default:i(()=>[...h[23]||=[g(` Tải giao dịch thay vì dữ liệu OHLCV `,-1)]]),_:1},8,[`modelValue`]),l(`div`,Ae,[s(o).activeBot.botFeatures.downloadDataCandleTypes?(r(),m(X,{key:0,modelValue:s(k).candleTypes,"onUpdate:modelValue":h[10]||=e=>s(k).candleTypes=e,options:P,"option-label":`text`,"option-value":`value`,placeholder:`Chọn loại nến`},null,8,[`modelValue`])):y(``,!0),h[24]||=l(`small`,null,`Khi không chọn loại nến, freqtrade tự động tải các loại nến cần thiết cho vận hành thông thường.`,-1)])]),l(`div`,je,[f(W,{modelValue:s(w).customExchange,"onUpdate:modelValue":h[11]||=e=>s(w).customExchange=e,class:`mb-2`},{default:i(()=>[...h[25]||=[g(` Sàn tùy chỉnh `,-1)]]),_:1},8,[`modelValue`]),f(b,{name:`fade`},{default:i(()=>[e(f(Z,{modelValue:s(w).selectedExchange,"onUpdate:modelValue":h[12]||=e=>s(w).selectedExchange=e},null,8,[`modelValue`]),[[x,s(w).customExchange]])]),_:1})])],512),[[x,s(N)]])]),_:1})]),l(`div`,Me,[f(O,{severity:`primary`,onClick:U},{default:i(()=>[...h[26]||=[g(`Bắt đầu tải`,-1)]]),_:1})])])])]),_:1})])}}}),Pe={};function Fe(e,t){let n=Ne;return r(),m(n,{class:`pt-4`})}var Ie=P(Pe,[[`render`,Fe]]);export{Ie as default};
//# sourceMappingURL=DownloadDataView-DapQjnjd.js.map