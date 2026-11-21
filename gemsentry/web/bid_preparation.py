"""Admin-only API for offline bid preparation. No GeM portal submission actions."""
from __future__ import annotations

import os

from flask import Blueprint, g, jsonify, request, send_file, render_template_string

from gemsentry import storage
from gemsentry.bid_preparation import service
from gemsentry.web.context import fail

bid_preparation_bp = Blueprint("bid_preparation", __name__)


def _admin_error():
    user = getattr(g, "current_user", None)
    if not user:
        return jsonify({"error": "Authentication required."}), 401
    if user.get("role") != "admin":
        return jsonify({"error": "Forbidden: administrator access required."}), 403
    return None


def _actor():
    user = getattr(g, "current_user", {}) or {}
    return user.get("username") or user.get("id") or "admin"


@bid_preparation_bp.before_request
def _require_admin():
    return _admin_error()


@bid_preparation_bp.get("/api/crm/bid-preparation")
def list_preparations():
    try:
        return jsonify({"preparations": service.list_all()})
    except Exception as exc:
        return fail(exc)


@bid_preparation_bp.route("/api/crm/bid-preparation/<path:bid_no>", methods=["GET", "PUT"])
def bid_preparation_detail(bid_no):
    try:
        bid_no = service.validate_bid(bid_no)
        tender = storage.get_record(bid_no)
        if tender is None:
            return jsonify({"error": "Tender not found in the active workspace."}), 404
        if request.method == "GET":
            return jsonify({"preparation": service.get(bid_no)})
        payload = request.get_json(silent=True)
        if payload is None:
            return jsonify({"error": "JSON object body is required."}), 400
        return jsonify({"preparation": service.save(bid_no, payload, _actor())})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return fail(exc)


@bid_preparation_bp.post("/api/crm/bid-preparation/<path:bid_no>/documents")
def upload_document(bid_no):
    try:
        bid_no = service.validate_bid(bid_no)
        if storage.get_record(bid_no) is None:
            return jsonify({"error": "Tender not found in the active workspace."}), 404
        uploaded = request.files.get("file")
        if uploaded is None:
            return jsonify({"error": "Choose a PDF file."}), 400
        result = service.add_document(bid_no, request.form.get("category", "other"),
                                      uploaded.filename, uploaded.read(25 * 1024 * 1024 + 1), _actor())
        return jsonify({"preparation": result}), 201
    except LookupError as exc:
        return jsonify({"error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return fail(exc)


@bid_preparation_bp.post("/api/crm/bid-preparation/documents/<int:document_id>/verification")
def verify_document(document_id):
    try:
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict) or not isinstance(payload.get("verified"), bool):
            return jsonify({"error": "verified must be true or false."}), 400
        return jsonify({"preparation": service.verify_document(document_id, payload["verified"], _actor())})
    except LookupError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:
        return fail(exc)


@bid_preparation_bp.get("/api/crm/bid-preparation/documents/<int:document_id>/download")
def download_document(document_id):
    try:
        found = service.document_path(document_id)
        if not found or not os.path.isfile(found[0]):
            return jsonify({"error": "Document not found."}), 404
        return send_file(found[0], as_attachment=True, download_name=found[1], mimetype="application/pdf")
    except Exception as exc:
        return fail(exc)



@bid_preparation_bp.get("/bid-preparation")
def bid_preparation_page():
    return render_template_string("""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>GeMSentry · Bid Preparation</title><style>
body{font:15px system-ui;background:#10131a;color:#e8edf5;margin:0;padding:28px}main{max-width:1100px;margin:auto}h1{margin:0 0 6px}p{color:#aab5c5}.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}.panel{background:#191f2a;border:1px solid #303a49;border-radius:12px;padding:18px;margin:18px 0}label{display:block;margin:10px 0 5px;color:#b9c4d3}input,select,textarea{box-sizing:border-box;width:100%;padding:10px;border-radius:7px;border:1px solid #3b4658;background:#111722;color:#fff}textarea{min-height:90px}button{background:#d73543;color:white;border:0;padding:10px 15px;border-radius:7px;cursor:pointer;margin:8px 6px 0 0}button.secondary{background:#38465b}.row{display:flex;gap:12px}.row>*{flex:1}.checks{display:grid;grid-template-columns:1fr 1fr;gap:5px}.checks label{margin:3px}.checks input{width:auto}table{width:100%;border-collapse:collapse}td,th{text-align:left;border-bottom:1px solid #303a49;padding:10px;vertical-align:top}.muted{color:#9aa8ba;font-size:13px}#msg{min-height:22px;color:#f0c674}@media(max-width:700px){.grid{grid-template-columns:1fr}.row{display:block}}
</style></head><body><main><h1>Bid Preparation Center</h1><p>Prepare and review bids offline. Final GeM submission, OTP and eSign remain manual.</p><div id="msg"></div>
<div class="grid"><section class="panel"><h2>Prepare a tender</h2><form id="prep"><label>GeM Bid Number</label><input id="bid" required maxlength="128" placeholder="GEM/2026/B/1234567"><div class="row"><div><label>Internal status</label><select id="status"><option value="draft">Draft</option><option value="in_review">In review</option><option value="ready_for_human_submission">Ready for human submission</option><option value="not_pursuing">Not pursuing</option></select></div><div><label>Quoted amount (INR)</label><input id="amount" type="number" min="0" step="0.01"></div></div><label>Internal notes</label><textarea id="notes" maxlength="10000" placeholder="Eligibility observations, staffing cost, PF/ESI, PSARA, EMD..."></textarea><label>Document checklist</label><div class="checks"><label><input type="checkbox" value="pan"> PAN</label><label><input type="checkbox" value="gst"> GST</label><label><input type="checkbox" value="pf_esi"> PF / ESI</label><label><input type="checkbox" value="labour_license"> Labour licence</label><label><input type="checkbox" value="psara"> PSARA</label><label><input type="checkbox" value="experience"> Experience proof</label><label><input type="checkbox" value="emd"> EMD / exemption</label><label><input type="checkbox" value="geographical_presence"> Geographical presence</label></div><button type="submit">Save preparation</button></form></section>
<section class="panel"><h2>Attach a PDF</h2><p class="muted">Save the preparation record first. Files are stored locally in the active workspace and are not sent to GeM.</p><label>Bid number</label><input id="docBid" placeholder="Same bid number"><label>Document category</label><select id="category"><option value="pan">PAN</option><option value="gst">GST</option><option value="pf_esi">PF / ESI</option><option value="labour_license">Labour licence</option><option value="psara">PSARA</option><option value="experience">Experience</option><option value="emd">EMD</option><option value="other">Other</option></select><label>PDF file (max 25 MB)</label><input id="file" type="file" accept="application/pdf,.pdf"><button id="upload">Upload PDF</button></section></div>
<section class="panel"><h2>Saved preparations</h2><button class="secondary" id="refresh">Refresh</button><div style="overflow:auto"><table><thead><tr><th>Bid number</th><th>Status</th><th>Quoted amount</th><th>Documents</th><th>Updated</th><th>Open</th></tr></thead><tbody id="rows"><tr><td colspan="6">Loading…</td></tr></tbody></table></div></section>
<section class="panel" id="detail" hidden><h2>Document review</h2><div id="docs"></div></section></main><script>
var $=function(id){return document.getElementById(id)}, msg=function(t){$("msg").textContent=t};
async function api(url,opts){var r=await fetch(url,opts||{}),d=await r.json().catch(function(){return {}});if(!r.ok)throw Error(d.error||"Request failed");return d}
function esc(s){return String(s==null?"":s).replace(/[&<>"']/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]})}
async function refresh(){try{var d=await api("/api/crm/bid-preparation");$("rows").innerHTML=d.preparations.map(function(p){return "<tr><td>"+esc(p.bid_no)+"</td><td>"+esc(p.status)+"</td><td>"+(p.quoted_amount==null?"—":p.quoted_amount)+" "+esc(p.currency)+"</td><td>"+p.document_count+"</td><td>"+esc(p.updated_at)+"</td><td><button onclick=\"openBid('"+encodeURIComponent(p.bid_no)+"')\">Review</button></td></tr>"}).join("")||'<tr><td colspan="6">No preparations yet.</td></tr>'}catch(e){msg(e.message)}}
$("prep").addEventListener("submit",async function(e){e.preventDefault();try{var bid=$("bid").value.trim(),checklist={};document.querySelectorAll(".checks input").forEach(function(x){checklist[x.value]=x.checked});var amount=$("amount").value;await api("/api/crm/bid-preparation/"+encodeURIComponent(bid),{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({status:$("status").value,quoted_amount:amount||null,notes:$("notes").value,checklist:checklist})});$("docBid").value=bid;msg("Preparation saved.");refresh()}catch(e){msg(e.message)}});
$("upload").addEventListener("click",async function(e){e.preventDefault();try{var bid=$("docBid").value.trim(),file=$("file").files[0];if(!bid||!file)throw Error("Enter bid number and choose a PDF.");var fd=new FormData();fd.append("file",file);fd.append("category",$("category").value);await api("/api/crm/bid-preparation/"+encodeURIComponent(bid)+"/documents",{method:"POST",body:fd});$("file").value="";msg("PDF registered.");openBid(encodeURIComponent(bid));refresh()}catch(e){msg(e.message)}});
window.openBid=async function(encoded){try{var bid=decodeURIComponent(encoded),d=await api("/api/crm/bid-preparation/"+encodeURIComponent(bid));$("detail").hidden=false;$("docs").innerHTML=(d.preparation&&d.preparation.documents||[]).map(function(x){return "<p><b>"+esc(x.category)+"</b> · "+esc(x.filename)+" · "+(x.verified?"Verified":"Not verified")+' <a href="/api/crm/bid-preparation/documents/'+x.id+'/download">Download</a> <button onclick="verifyDoc('+x.id+','+(!x.verified)+',\''+encodeURIComponent(bid)+'\')">'+(x.verified?"Unverify":"Mark verified")+"</button></p>"}).join("")||"No documents registered.";$("bid").value=bid;$("docBid").value=bid;$("detail").scrollIntoView({behavior:"smooth"})}catch(e){msg(e.message)}};
window.verifyDoc=async function(id,verified,bid){try{await api("/api/crm/bid-preparation/documents/"+id+"/verification",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({verified:verified})});msg("Document review updated.");openBid(bid)}catch(e){msg(e.message)}};
$("refresh").addEventListener("click",refresh);refresh();
</script></body></html>""")
