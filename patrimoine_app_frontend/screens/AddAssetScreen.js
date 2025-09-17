// AddAssetScreen.js
import React, { useState } from "react";
import { View, Text, TextInput, Button, StyleSheet, TouchableOpacity, ScrollView, Alert } from "react-native";

const TYPES = [
  { key: "livret", label: "Livret / Compte", color: "#2ecc71" },
  { key: "portfolio", label: "Actions / Fonds", color: "#0077b6" },
  { key: "immo", label: "Immobilier", color: "#ff7f50" },
  { key: "other", label: "Autres", color: "#9b59b6" }
];

// Replace with your backend base URL (Render)
const API_BASE = "https://patrimoine-backend-pngw.onrender.com/api";

export default function AddAssetScreen({ navigation }) {
  const [step, setStep] = useState(0);
  const [type, setType] = useState("livret");
  const [label, setLabel] = useState("");
  const [currentValue, setCurrentValue] = useState("");

  // shared details object
  const [details, setDetails] = useState({});

  // For portfolio lines (array)
  const [lineTmp, setLineTmp] = useState({ isin: "", label: "", amount_invested: "", units: "", purchase_date: "" });

  const setDetail = (k, v) => setDetails(prev => ({ ...prev, [k]: v }));

  const gotoNext = () => setStep(s => Math.min(2, s + 1));
  const gotoPrev = () => setStep(s => Math.max(0, s - 1));

  // Validate step 1 -> basic
  const validateStep1 = () => {
    if (!label || label.trim().length < 2) { Alert.alert("Erreur", "Donne un nom à ton actif"); return false; }
    if (!type) { Alert.alert("Erreur", "Choisis un type"); return false; }
    return true;
  };

  const submit = async () => {
    // build payload
    const payload = {
      type: type === "portfolio" ? "portfolio" : type === "livret" ? "livret" : type === "immo" ? "immo" : "other",
      label,
      current_value: currentValue ? parseFloat(currentValue) : null,
      details
    };

    // ensure numeric conversion for some fields
    if (payload.details.balance) payload.details.balance = parseFloat(payload.details.balance);
    if (payload.details.recurring_amount) payload.details.recurring_amount = parseFloat(payload.details.recurring_amount);

    try {
      const res = await fetch(`${API_BASE}/assets`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const j = await res.json();
      if (res.status >= 200 && res.status < 300) {
        Alert.alert("Succès", "Actif ajouté");
        navigation.navigate("Accueil"); // return to home
      } else {
        Alert.alert("Erreur serveur", JSON.stringify(j));
      }
    } catch (e) {
      Alert.alert("Erreur réseau", e.message);
    }
  };

  const addLineToDetails = () => {
    if (!lineTmp.isin && !lineTmp.label) { Alert.alert("Erreur", "Remplis ISIN ou un label"); return; }
    const lines = details.lines ? [...details.lines] : [];
    lines.push({
      isin: lineTmp.isin || null,
      label: lineTmp.label || null,
      amount_invested: lineTmp.amount_invested ? parseFloat(lineTmp.amount_invested) : null,
      units: lineTmp.units ? parseFloat(lineTmp.units) : null,
      purchase_date: lineTmp.purchase_date || null
    });
    setDetails({ ...details, lines });
    setLineTmp({ isin: "", label: "", amount_invested: "", units: "", purchase_date: "" });
  };

  // Renders form for each type
  const renderFormForType = () => {
    switch (type) {
      case "livret":
        return (
          <>
            <Text style={styles.label}>Banque</Text>
            <TextInput style={styles.input} placeholder="Ex: BNP Paribas" value={details.bank || ""} onChangeText={(t)=>setDetail("bank", t)} />
            <Text style={styles.label}>Solde actuel (€)</Text>
            <TextInput style={styles.input} keyboardType="numeric" placeholder="12000" value={details.balance ? String(details.balance) : ""} onChangeText={(t)=>setDetail("balance", t)} />
            <Text style={styles.label}>Versements récurrents ?</Text>
            <TextInput style={styles.input} keyboardType="numeric" placeholder="Montant (ex: 200)" value={details.recurring_amount ? String(details.recurring_amount) : ""} onChangeText={(t)=>setDetail("recurring_amount", t)} />
            <Text style={styles.noteSmall}>Fréquence (ex: mensuel)</Text>
            <TextInput style={styles.input} placeholder="mensuel / trimestriel" value={details.recurring_frequency || ""} onChangeText={(t)=>setDetail("recurring_frequency", t)} />
            <Text style={styles.label}>Jour du versement</Text>
            <TextInput style={styles.input} placeholder="5" keyboardType="numeric" value={details.recurring_day ? String(details.recurring_day) : ""} onChangeText={(t)=>setDetail("recurring_day", t)} />
          </>
        );
      case "portfolio":
        return (
          <>
            <Text style={styles.label}>Produit (PEA / CTO / AV / PER)</Text>
            <TextInput style={styles.input} placeholder="PEA" value={details.product_type || ""} onChangeText={(t)=>setDetail("product_type", t)} />
            <Text style={styles.label}>Courtier</Text>
            <TextInput style={styles.input} placeholder="Boursorama" value={details.broker || ""} onChangeText={(t)=>setDetail("broker", t)} />
            <Text style={styles.label}>Versement initial (€)</Text>
            <TextInput style={styles.input} keyboardType="numeric" value={details.initial_investment ? String(details.initial_investment) : ""} onChangeText={(t)=>setDetail("initial_investment", t)} />
            <Text style={styles.titleSmall}>Lignes de portefeuille (ISIN / montant)</Text>
            <TextInput style={styles.input} placeholder="ISIN" value={lineTmp.isin} onChangeText={(t)=>setLineTmp({...lineTmp, isin: t})} />
            <TextInput style={styles.input} placeholder="Libellé" value={lineTmp.label} onChangeText={(t)=>setLineTmp({...lineTmp, label: t})} />
            <TextInput style={styles.input} placeholder="Montant investi" keyboardType="numeric" value={lineTmp.amount_invested} onChangeText={(t)=>setLineTmp({...lineTmp, amount_invested: t})} />
            <Button title="Ajouter la ligne" onPress={addLineToDetails} />
            {details.lines && details.lines.length > 0 && (
              <View style={{marginTop:10}}>
                <Text style={{fontWeight:'600'}}>Lignes ajoutées :</Text>
                {details.lines.map((l, idx)=>(
                  <Text key={idx}>{l.isin || l.label} — {l.amount_invested || ''}€</Text>
                ))}
              </View>
            )}
          </>
        );
      case "immo":
        return (
          <>
            <Text style={styles.label}>Adresse</Text>
            <TextInput style={styles.input} placeholder="Adresse complète" value={details.address || ""} onChangeText={(t)=>setDetail("address", t)} />
            <Text style={styles.label}>Prix d'achat (€)</Text>
            <TextInput style={styles.input} keyboardType="numeric" value={details.purchase_price ? String(details.purchase_price) : ""} onChangeText={(t)=>setDetail("purchase_price", t)} />
            <Text style={styles.label}>Frais notaire (€)</Text>
            <TextInput style={styles.input} keyboardType="numeric" value={details.notary_fees ? String(details.notary_fees) : ""} onChangeText={(t)=>setDetail("notary_fees", t)} />
            <Text style={styles.label}>Apport (€)</Text>
            <TextInput style={styles.input} keyboardType="numeric" value={details.down_payment ? String(details.down_payment) : ""} onChangeText={(t)=>setDetail("down_payment", t)} />
            <Text style={styles.label}>Crédit associé ? (si oui renseigne le bloc)</Text>
            <Text style={styles.sub}>Montant du prêt</Text>
            <TextInput style={styles.input} keyboardType="numeric" value={details.loan_amount ? String(details.loan_amount) : ""} onChangeText={(t)=>setDetail("loan_amount", t)} />
            <Text style={styles.sub}>Taux annuel (%)</Text>
            <TextInput style={styles.input} keyboardType="numeric" value={details.loan_rate ? String(details.loan_rate) : ""} onChangeText={(t)=>setDetail("loan_rate", t)} />
            <Text style={styles.sub}>Durée (mois)</Text>
            <TextInput style={styles.input} keyboardType="numeric" value={details.loan_duration_months ? String(details.loan_duration_months) : ""} onChangeText={(t)=>setDetail("loan_duration_months", t)} />
            <Text style={styles.sub}>Mensualité (laisser vide pour calcul automatique)</Text>
            <TextInput style={styles.input} keyboardType="numeric" value={details.monthly_payment ? String(details.monthly_payment) : ""} onChangeText={(t)=>setDetail("monthly_payment", t)} />
            <Text style={styles.sub}>Loyer mensuel (si locatif)</Text>
            <TextInput style={styles.input} keyboardType="numeric" value={details.rental_income ? String(details.rental_income) : ""} onChangeText={(t)=>setDetail("rental_income", t)} />
          </>
        );
      case "other":
        return (
          <>
            <Text style={styles.label}>Catégorie (crypto / or / oeuvre)</Text>
            <TextInput style={styles.input} placeholder="crypto" value={details.category || ""} onChangeText={(t)=>setDetail("category", t)} />
            <Text style={styles.label}>Valeur estimée (€)</Text>
            <TextInput style={styles.input} keyboardType="numeric" value={details.estimated_value ? String(details.estimated_value) : ""} onChangeText={(t)=>setDetail("estimated_value", t)} />
            <Text style={styles.label}>Description</Text>
            <TextInput style={styles.input} value={details.description || ""} onChangeText={(t)=>setDetail("description", t)} />
          </>
        );
      default:
        return null;
    }
  };

  const renderStep = () => {
    if (step === 0) {
      // Choose type & basic naming
      return (
        <View>
          <Text style={styles.title}>Quel type d'actif veux-tu ajouter ?</Text>
          <View style={styles.typeRow}>
            {TYPES.map(t => (
              <TouchableOpacity key={t.key} style={[styles.typeBtn, {borderColor: type === t.key ? t.color : "#eee"}]} onPress={()=>{ setType(t.key); setDetails({}); }}>
                <Text style={{color:type===t.key?t.color:'#333', fontWeight:'600'}}>{t.label}</Text>
              </TouchableOpacity>
            ))}
          </View>
          <Text style={styles.label}>Nom (ex : 'Livret A Crédit Agricole')</Text>
          <TextInput style={styles.input} value={label} onChangeText={setLabel} />
          <Text style={styles.label}>Valeur actuelle (optionnel)</Text>
          <TextInput style={styles.input} keyboardType="numeric" value={currentValue} onChangeText={setCurrentValue} />
          <View style={{marginTop:12}}>
            <Button title="Suivant" onPress={() => { if (validateStep1()) gotoNext(); }} />
          </View>
        </View>
      );
    } else if (step === 1) {
      // specific form
      return (
        <ScrollView style={{maxHeight: 520}}>
          {renderFormForType()}
          <View style={{flexDirection:'row', justifyContent:'space-between', marginTop:16}}>
            <Button title="Précédent" onPress={gotoPrev} />
            <Button title="Résumé" onPress={gotoNext} />
          </View>
        </ScrollView>
      );
    } else {
      // step 2 : summary & submit
      return (
        <View>
          <Text style={styles.title}>Résumé</Text>
          <Text style={{fontWeight:'600'}}>{label} — {type}</Text>
          <Text>Valeur actuelle : {currentValue ? `${currentValue} €` : "Non renseignée"}</Text>
          <Text style={{marginTop:8, fontWeight:'600'}}>Détails</Text>
          <Text>{JSON.stringify(details, null, 2)}</Text>
          <View style={{flexDirection:'row', justifyContent:'space-between', marginTop:16}}>
            <Button title="Modifier" onPress={gotoPrev} />
            <Button title="Enregistrer" onPress={submit} />
          </View>
        </View>
      );
    }
  };

  return (
    <View style={styles.wrapper}>
      <View style={styles.header}>
        <Text style={styles.headerTitle}>Ajouter un actif</Text>
        <Text style={styles.step}>Étape {step+1} / 3</Text>
      </View>
      <View style={styles.container}>
        {renderStep()}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  wrapper: {flex:1, padding:16, backgroundColor:'#fff'},
  header: {flexDirection:'row', justifyContent:'space-between', alignItems:'center', marginBottom:10},
  headerTitle: {fontSize:18, fontWeight:'700'},
  step: {color:'#666'},
  container: {flex:1},
  typeRow: {flexDirection:'row', justifyContent:'space-between', marginVertical:12},
  typeBtn: {padding:12, borderWidth:1, borderRadius:10, width:'23%', alignItems:'center'},
  title: {fontSize:16, marginBottom:12},
  input: {borderWidth:1, borderColor:'#ddd', padding:8, borderRadius:8, marginBottom:10},
  label: {fontSize:13, color:'#333', marginBottom:6},
  sub: {fontSize:12, color:'#666', marginTop:6},
  noteSmall: {fontSize:12, color:'#666', marginBottom:6},
  titleSmall: {fontWeight:'600', marginTop:8, marginBottom:6}
});
