import React from "react";
import { View, Text, StyleSheet } from "react-native";

export default function DetailsScreen() {
  return (
    <View style={styles.container}>
      <Text style={styles.text}>Détails de la répartition (à venir)</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, justifyContent: "center", alignItems: "center" },
  text: { fontSize: 18 },
});
